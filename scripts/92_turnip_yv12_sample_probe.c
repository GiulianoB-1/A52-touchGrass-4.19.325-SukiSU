#include <android/hardware_buffer.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_android.h>

#include "yv12_sample_spv.h"

#ifndef AHARDWAREBUFFER_FORMAT_YV12
#define AHARDWAREBUFFER_FORMAT_YV12 0x32315659u
#endif

#define TEST_W 940u
#define TEST_H 1670u

static const char *vk_result_name(VkResult r)
{
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_MEMORY_MAP_FAILED: return "VK_ERROR_MEMORY_MAP_FAILED";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_FORMAT_NOT_SUPPORTED: return "VK_ERROR_FORMAT_NOT_SUPPORTED";
    case (VkResult)-1000072003: return "VK_ERROR_INVALID_EXTERNAL_HANDLE";
    case (VkResult)-1000158000: return "VK_ERROR_INVALID_DRM_FORMAT_MODIFIER_PLANE_LAYOUT_EXT";
    default: return "VK_RESULT_OTHER";
    }
}

static uint32_t first_set_bit(uint32_t bits)
{
    for (uint32_t i = 0; i < 32; ++i)
        if (bits & (1u << i))
            return i;
    return UINT32_MAX;
}

static uint32_t choose_host_visible_memory(VkPhysicalDevice physical,
                                           uint32_t bits,
                                           VkMemoryPropertyFlags *flags_out)
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(physical, &mp);

    for (uint32_t pass = 0; pass < 2; ++pass) {
        for (uint32_t i = 0; i < mp.memoryTypeCount; ++i) {
            if (!(bits & (1u << i)))
                continue;
            VkMemoryPropertyFlags f = mp.memoryTypes[i].propertyFlags;
            if (!(f & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT))
                continue;
            if (pass == 0 && !(f & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT))
                continue;
            if (flags_out)
                *flags_out = f;
            return i;
        }
    }

    return UINT32_MAX;
}

static int fill_yv12_pattern(AHardwareBuffer *ahb)
{
    AHardwareBuffer_Planes planes;
    memset(&planes, 0, sizeof(planes));

    int rc = AHardwareBuffer_lockPlanes(
        ahb, AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY, -1, NULL, &planes);
    printf("yv12_sample.ahb_lock_planes=%d\n", rc);
    if (rc != 0)
        return 1;

    printf("yv12_sample.plane_count=%u\n", planes.planeCount);
    if (planes.planeCount < 3) {
        AHardwareBuffer_unlock(ahb, NULL);
        return 2;
    }

    for (uint32_t p = 0; p < planes.planeCount; ++p) {
        printf("yv12_sample.plane[%u].rowStride=%u\n", p, planes.planes[p].rowStride);
        printf("yv12_sample.plane[%u].pixelStride=%u\n", p, planes.planes[p].pixelStride);
        intptr_t off = (uint8_t *)planes.planes[p].data -
                       (uint8_t *)planes.planes[0].data;
        printf("yv12_sample.plane[%u].offset_from_plane0=%" PRIdPTR "\n", p, off);
    }

    /* Luma stays constant. Chroma varies by row so a wrong 480 -> 512 pitch
     * interpretation produces a visibly different sampled RGB value.
     * Both chroma planes use the same value, making the test independent of
     * whether lockPlanes reports the physical Y-V-U or logical Y-U-V order.
     */
    for (uint32_t y = 0; y < TEST_H; ++y) {
        uint8_t *row = (uint8_t *)planes.planes[0].data +
                       (size_t)y * planes.planes[0].rowStride;
        memset(row, 128, planes.planes[0].rowStride);
    }

    const uint32_t chroma_h = TEST_H / 2u;
    for (uint32_t p = 1; p < 3; ++p) {
        for (uint32_t y = 0; y < chroma_h; ++y) {
            uint8_t value = (uint8_t)(64u + (y % 128u));
            uint8_t *row = (uint8_t *)planes.planes[p].data +
                           (size_t)y * planes.planes[p].rowStride;
            memset(row, value, planes.planes[p].rowStride);
        }
    }

    rc = AHardwareBuffer_unlock(ahb, NULL);
    printf("yv12_sample.ahb_unlock=%d\n", rc);
    return rc == 0 ? 0 : 3;
}

static float absf_local(float x)
{
    return x < 0.0f ? -x : x;
}

static int write_reference(const char *path, const float rgba[4])
{
    FILE *fp = fopen(path, "w");
    if (!fp)
        return -1;
    int ok = fprintf(fp, "%.9f %.9f %.9f %.9f\n",
                     rgba[0], rgba[1], rgba[2], rgba[3]) > 0;
    if (fclose(fp) != 0)
        ok = 0;
    return ok ? 0 : -1;
}

static int read_reference(const char *path, float rgba[4])
{
    FILE *fp = fopen(path, "r");
    if (!fp)
        return -1;
    int n = fscanf(fp, "%f %f %f %f",
                   &rgba[0], &rgba[1], &rgba[2], &rgba[3]);
    fclose(fp);
    return n == 4 ? 0 : -1;
}

int main(int argc, char **argv)
{
    int import_before_fill = 0;
    const char *write_ref_path = NULL;
    const char *compare_ref_path = NULL;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--import-before-fill") == 0) {
            import_before_fill = 1;
        } else if (strcmp(argv[i], "--write-ref") == 0 && i + 1 < argc) {
            write_ref_path = argv[++i];
        } else if (strcmp(argv[i], "--compare-ref") == 0 && i + 1 < argc) {
            compare_ref_path = argv[++i];
        }
    }

    setvbuf(stdout, NULL, _IONBF, 0);
    printf("========== YV12 940x1670 GPU SAMPLE PROBE ==========\n");
    printf("yv12_sample.import_before_fill=%d\n", import_before_fill);

    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory image_mem = VK_NULL_HANDLE;
    VkSamplerYcbcrConversion conversion = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkSampler sampler = VK_NULL_HANDLE;
    VkBuffer out_buffer = VK_NULL_HANDLE;
    VkDeviceMemory out_mem = VK_NULL_HANDLE;
    void *mapped = NULL;
    VkDescriptorSetLayout dsl = VK_NULL_HANDLE;
    VkDescriptorPool dpool = VK_NULL_HANDLE;
    VkPipelineLayout pipeline_layout = VK_NULL_HANDLE;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkCommandPool cmd_pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    AHardwareBuffer *ahb = NULL;
    int ret = 99;

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "touchGrass-YV12-Sample",
        .applicationVersion = 1,
        .pEngineName = "touchGrass",
        .engineVersion = 1,
        .apiVersion = VK_API_VERSION_1_1,
    };
    VkInstanceCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };

    VkResult r = vkCreateInstance(&ici, NULL, &instance);
    printf("yv12_sample.vkCreateInstance=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 10; goto cleanup; }

    uint32_t physical_count = 0;
    r = vkEnumeratePhysicalDevices(instance, &physical_count, NULL);
    if (r != VK_SUCCESS || physical_count == 0) { ret = 11; goto cleanup; }
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    physical_count = 1;
    r = vkEnumeratePhysicalDevices(instance, &physical_count, &physical);
    if (r != VK_SUCCESS) { ret = 12; goto cleanup; }

    VkPhysicalDeviceProperties props_dev;
    vkGetPhysicalDeviceProperties(physical, &props_dev);
    printf("yv12_sample.device_name=%s\n", props_dev.deviceName);
    printf("yv12_sample.device_api=%u.%u.%u\n",
           VK_API_VERSION_MAJOR(props_dev.apiVersion),
           VK_API_VERSION_MINOR(props_dev.apiVersion),
           VK_API_VERSION_PATCH(props_dev.apiVersion));

    uint32_t qcount = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &qcount, NULL);
    VkQueueFamilyProperties *qprops = calloc(qcount, sizeof(*qprops));
    if (!qprops) { ret = 13; goto cleanup; }
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &qcount, qprops);
    uint32_t qfi = UINT32_MAX;
    for (uint32_t i = 0; i < qcount; ++i) {
        if ((qprops[i].queueFlags & VK_QUEUE_COMPUTE_BIT) &&
            (qprops[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) {
            qfi = i;
            break;
        }
    }
    free(qprops);
    if (qfi == UINT32_MAX) { ret = 14; goto cleanup; }

    VkPhysicalDeviceSamplerYcbcrConversionFeatures ycbcr_feature = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES,
    };
    VkPhysicalDeviceFeatures2 features2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &ycbcr_feature,
    };
    vkGetPhysicalDeviceFeatures2(physical, &features2);
    printf("yv12_sample.samplerYcbcrConversion_feature=%u\n",
           ycbcr_feature.samplerYcbcrConversion);
    if (!ycbcr_feature.samplerYcbcrConversion) { ret = 15; goto cleanup; }

    float priority = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = qfi,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };
    const char *exts[] = {
        VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME,
    };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext = &ycbcr_feature,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
        .enabledExtensionCount = 1,
        .ppEnabledExtensionNames = exts,
    };
    r = vkCreateDevice(physical, &dci, NULL, &device);
    printf("yv12_sample.vkCreateDevice=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 16; goto cleanup; }

    VkQueue queue = VK_NULL_HANDLE;
    vkGetDeviceQueue(device, qfi, 0, &queue);

    AHardwareBuffer_Desc desc = {
        .width = TEST_W,
        .height = TEST_H,
        .layers = 1,
        .format = AHARDWAREBUFFER_FORMAT_YV12,
        .usage = AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
                 AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY,
    };
    printf("yv12_sample.ahb_is_supported=%d\n", AHardwareBuffer_isSupported(&desc));
    int ahb_rc = AHardwareBuffer_allocate(&desc, &ahb);
    printf("yv12_sample.ahb_allocate=%d\n", ahb_rc);
    if (ahb_rc != 0 || !ahb) { ret = 17; goto cleanup; }

    AHardwareBuffer_Desc got = {0};
    AHardwareBuffer_describe(ahb, &got);
    printf("yv12_sample.desc_stride=%u\n", got.stride);

    if (!import_before_fill) {
        if (fill_yv12_pattern(ahb) != 0) { ret = 18; goto cleanup; }
    }

    PFN_vkGetAndroidHardwareBufferPropertiesANDROID get_ahb_props =
        (PFN_vkGetAndroidHardwareBufferPropertiesANDROID)
        vkGetDeviceProcAddr(device, "vkGetAndroidHardwareBufferPropertiesANDROID");
    if (!get_ahb_props) { ret = 19; goto cleanup; }

    VkAndroidHardwareBufferFormatPropertiesANDROID fmt = {
        .sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_FORMAT_PROPERTIES_ANDROID,
    };
    VkAndroidHardwareBufferPropertiesANDROID ahb_props = {
        .sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID,
        .pNext = &fmt,
    };
    r = get_ahb_props(device, ahb, &ahb_props);
    printf("yv12_sample.vkGetAndroidHardwareBufferPropertiesANDROID=%d:%s\n",
           r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 20; goto cleanup; }

    printf("yv12_sample.externalFormat=%" PRIu64 "\n", (uint64_t)fmt.externalFormat);
    printf("yv12_sample.ycbcrModel=%d\n", fmt.suggestedYcbcrModel);
    printf("yv12_sample.ycbcrRange=%d\n", fmt.suggestedYcbcrRange);
    printf("yv12_sample.xChromaOffset=%d\n", fmt.suggestedXChromaOffset);
    printf("yv12_sample.yChromaOffset=%d\n", fmt.suggestedYChromaOffset);

    VkExternalFormatANDROID external_format = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_FORMAT_ANDROID,
        .externalFormat = fmt.externalFormat,
    };
    VkExternalMemoryImageCreateInfo external_mem = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
        .pNext = &external_format,
        .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID,
    };
    VkImageCreateInfo image_ci = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .pNext = &external_mem,
        .imageType = VK_IMAGE_TYPE_2D,
        .format = VK_FORMAT_UNDEFINED,
        .extent = { TEST_W, TEST_H, 1 },
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_SAMPLED_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    r = vkCreateImage(device, &image_ci, NULL, &image);
    printf("yv12_sample.vkCreateImage=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 21; goto cleanup; }

    VkMemoryRequirements image_req;
    vkGetImageMemoryRequirements(device, image, &image_req);
    uint32_t image_type = first_set_bit(image_req.memoryTypeBits & ahb_props.memoryTypeBits);
    if (image_type == UINT32_MAX) { ret = 22; goto cleanup; }

    VkImportAndroidHardwareBufferInfoANDROID import_info = {
        .sType = VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID,
        .buffer = ahb,
    };
    VkMemoryDedicatedAllocateInfo dedicated = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
        .pNext = &import_info,
        .image = image,
    };
    VkMemoryAllocateInfo image_mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext = &dedicated,
        .allocationSize = ahb_props.allocationSize,
        .memoryTypeIndex = image_type,
    };
    r = vkAllocateMemory(device, &image_mai, NULL, &image_mem);
    printf("yv12_sample.vkAllocateMemory_AHB=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 23; goto cleanup; }
    r = vkBindImageMemory(device, image, image_mem, 0);
    printf("yv12_sample.vkBindImageMemory=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 24; goto cleanup; }

    if (import_before_fill) {
        printf("yv12_sample.fill_after_bind=1\n");
        if (fill_yv12_pattern(ahb) != 0) { ret = 18; goto cleanup; }
    }

    VkExternalFormatANDROID conv_external = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_FORMAT_ANDROID,
        .externalFormat = fmt.externalFormat,
    };
    VkSamplerYcbcrConversionCreateInfo conv_ci = {
        .sType = VK_STRUCTURE_TYPE_SAMPLER_YCBCR_CONVERSION_CREATE_INFO,
        .pNext = &conv_external,
        .format = VK_FORMAT_UNDEFINED,
        .ycbcrModel = fmt.suggestedYcbcrModel,
        .ycbcrRange = fmt.suggestedYcbcrRange,
        .components = fmt.samplerYcbcrConversionComponents,
        .xChromaOffset = fmt.suggestedXChromaOffset,
        .yChromaOffset = fmt.suggestedYChromaOffset,
        .chromaFilter = VK_FILTER_NEAREST,
        .forceExplicitReconstruction = VK_FALSE,
    };
    r = vkCreateSamplerYcbcrConversion(device, &conv_ci, NULL, &conversion);
    printf("yv12_sample.vkCreateSamplerYcbcrConversion=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 25; goto cleanup; }

    VkSamplerYcbcrConversionInfo conv_info = {
        .sType = VK_STRUCTURE_TYPE_SAMPLER_YCBCR_CONVERSION_INFO,
        .conversion = conversion,
    };
    VkImageViewCreateInfo view_ci = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .pNext = &conv_info,
        .image = image,
        .viewType = VK_IMAGE_VIEW_TYPE_2D,
        .format = VK_FORMAT_UNDEFINED,
        .components = {
            VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY,
            VK_COMPONENT_SWIZZLE_IDENTITY, VK_COMPONENT_SWIZZLE_IDENTITY,
        },
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0, .levelCount = 1,
            .baseArrayLayer = 0, .layerCount = 1,
        },
    };
    r = vkCreateImageView(device, &view_ci, NULL, &view);
    printf("yv12_sample.vkCreateImageView=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 26; goto cleanup; }

    VkSamplerCreateInfo sampler_ci = {
        .sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO,
        .pNext = &conv_info,
        .magFilter = VK_FILTER_NEAREST,
        .minFilter = VK_FILTER_NEAREST,
        .mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST,
        .addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
        .maxLod = 0.0f,
    };
    r = vkCreateSampler(device, &sampler_ci, NULL, &sampler);
    printf("yv12_sample.vkCreateSampler=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 27; goto cleanup; }

    VkBufferCreateInfo out_bci = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = 16,
        .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    r = vkCreateBuffer(device, &out_bci, NULL, &out_buffer);
    printf("yv12_sample.vkCreateOutputBuffer=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 28; goto cleanup; }

    VkMemoryRequirements out_req;
    vkGetBufferMemoryRequirements(device, out_buffer, &out_req);
    VkMemoryPropertyFlags out_flags = 0;
    uint32_t out_type = choose_host_visible_memory(
        physical, out_req.memoryTypeBits, &out_flags);
    if (out_type == UINT32_MAX) { ret = 29; goto cleanup; }

    VkMemoryAllocateInfo out_mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = out_req.size,
        .memoryTypeIndex = out_type,
    };
    r = vkAllocateMemory(device, &out_mai, NULL, &out_mem);
    if (r != VK_SUCCESS) { ret = 30; goto cleanup; }
    r = vkBindBufferMemory(device, out_buffer, out_mem, 0);
    if (r != VK_SUCCESS) { ret = 31; goto cleanup; }
    r = vkMapMemory(device, out_mem, 0, VK_WHOLE_SIZE, 0, &mapped);
    if (r != VK_SUCCESS || !mapped) { ret = 32; goto cleanup; }

    float *out = (float *)mapped;
    out[0] = out[1] = out[2] = out[3] = -99.0f;
    if (!(out_flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
        VkMappedMemoryRange range = {
            .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
            .memory = out_mem, .offset = 0, .size = VK_WHOLE_SIZE,
        };
        vkFlushMappedMemoryRanges(device, 1, &range);
    }

    VkDescriptorSetLayoutBinding bindings[2] = {
        {
            .binding = 0,
            .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,
            .descriptorCount = 1,
            .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
            .pImmutableSamplers = &sampler,
        },
        {
            .binding = 1,
            .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .descriptorCount = 1,
            .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
        },
    };
    VkDescriptorSetLayoutCreateInfo dsl_ci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
        .bindingCount = 2,
        .pBindings = bindings,
    };
    r = vkCreateDescriptorSetLayout(device, &dsl_ci, NULL, &dsl);
    printf("yv12_sample.vkCreateDescriptorSetLayout=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 33; goto cleanup; }

    VkDescriptorPoolSize pool_sizes[2] = {
        { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1 },
        { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1 },
    };
    VkDescriptorPoolCreateInfo dp_ci = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
        .maxSets = 1,
        .poolSizeCount = 2,
        .pPoolSizes = pool_sizes,
    };
    r = vkCreateDescriptorPool(device, &dp_ci, NULL, &dpool);
    if (r != VK_SUCCESS) { ret = 34; goto cleanup; }

    VkDescriptorSetAllocateInfo ds_ai = {
        .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
        .descriptorPool = dpool,
        .descriptorSetCount = 1,
        .pSetLayouts = &dsl,
    };
    VkDescriptorSet ds = VK_NULL_HANDLE;
    r = vkAllocateDescriptorSets(device, &ds_ai, &ds);
    if (r != VK_SUCCESS) { ret = 35; goto cleanup; }

    VkDescriptorImageInfo dii = {
        .sampler = sampler,
        .imageView = view,
        .imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
    };
    VkDescriptorBufferInfo dbi = {
        .buffer = out_buffer,
        .offset = 0,
        .range = 16,
    };
    VkWriteDescriptorSet writes[2] = {
        {
            .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
            .dstSet = ds, .dstBinding = 0,
            .descriptorCount = 1,
            .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,
            .pImageInfo = &dii,
        },
        {
            .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,
            .dstSet = ds, .dstBinding = 1,
            .descriptorCount = 1,
            .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,
            .pBufferInfo = &dbi,
        },
    };
    vkUpdateDescriptorSets(device, 2, writes, 0, NULL);

    VkPipelineLayoutCreateInfo pl_ci = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = 1,
        .pSetLayouts = &dsl,
    };
    r = vkCreatePipelineLayout(device, &pl_ci, NULL, &pipeline_layout);
    if (r != VK_SUCCESS) { ret = 36; goto cleanup; }

    VkShaderModuleCreateInfo sm_ci = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = yv12_sample_spv_size,
        .pCode = yv12_sample_spv,
    };
    r = vkCreateShaderModule(device, &sm_ci, NULL, &shader);
    printf("yv12_sample.vkCreateShaderModule=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 37; goto cleanup; }

    VkComputePipelineCreateInfo cp_ci = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT,
            .module = shader,
            .pName = "main",
        },
        .layout = pipeline_layout,
    };
    r = vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cp_ci, NULL, &pipeline);
    printf("yv12_sample.vkCreateComputePipelines=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 38; goto cleanup; }

    VkCommandPoolCreateInfo pool_ci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .queueFamilyIndex = qfi,
    };
    r = vkCreateCommandPool(device, &pool_ci, NULL, &cmd_pool);
    if (r != VK_SUCCESS) { ret = 39; goto cleanup; }

    VkCommandBufferAllocateInfo cb_ai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = cmd_pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    VkCommandBuffer cb = VK_NULL_HANDLE;
    r = vkAllocateCommandBuffers(device, &cb_ai, &cb);
    if (r != VK_SUCCESS) { ret = 40; goto cleanup; }

    VkCommandBufferBeginInfo begin = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    r = vkBeginCommandBuffer(cb, &begin);
    if (r != VK_SUCCESS) { ret = 41; goto cleanup; }

    VkImageMemoryBarrier acquire = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcAccessMask = 0,
        .dstAccessMask = VK_ACCESS_SHADER_READ_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_GENERAL,
        .newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_EXTERNAL,
        .dstQueueFamilyIndex = qfi,
        .image = image,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0, .levelCount = 1,
            .baseArrayLayer = 0, .layerCount = 1,
        },
    };
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                         VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0,
                         0, NULL, 0, NULL, 1, &acquire);

    vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE,
                            pipeline_layout, 0, 1, &ds, 0, NULL);
    vkCmdDispatch(cb, 1, 1, 1);

    VkBufferMemoryBarrier host_barrier = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = out_buffer,
        .offset = 0,
        .size = 16,
    };
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                         VK_PIPELINE_STAGE_HOST_BIT, 0,
                         0, NULL, 1, &host_barrier, 0, NULL);

    VkImageMemoryBarrier release = acquire;
    release.srcAccessMask = VK_ACCESS_SHADER_READ_BIT;
    release.dstAccessMask = 0;
    release.oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    release.newLayout = VK_IMAGE_LAYOUT_GENERAL;
    release.srcQueueFamilyIndex = qfi;
    release.dstQueueFamilyIndex = VK_QUEUE_FAMILY_EXTERNAL;
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                         VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0,
                         0, NULL, 0, NULL, 1, &release);

    r = vkEndCommandBuffer(cb);
    if (r != VK_SUCCESS) { ret = 42; goto cleanup; }

    VkFenceCreateInfo fence_ci = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    r = vkCreateFence(device, &fence_ci, NULL, &fence);
    if (r != VK_SUCCESS) { ret = 43; goto cleanup; }

    VkSubmitInfo submit = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &cb,
    };
    printf("yv12_sample.before_vkQueueSubmit=1\n");
    r = vkQueueSubmit(queue, 1, &submit, fence);
    printf("yv12_sample.vkQueueSubmit=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 44; goto cleanup; }

    r = vkWaitForFences(device, 1, &fence, VK_TRUE, 5000000000ULL);
    printf("yv12_sample.vkWaitForFences=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) { ret = 45; goto cleanup; }

    if (!(out_flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
        VkMappedMemoryRange range = {
            .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
            .memory = out_mem, .offset = 0, .size = VK_WHOLE_SIZE,
        };
        vkInvalidateMappedMemoryRanges(device, 1, &range);
    }

    printf("yv12_sample.rgba=%.6f,%.6f,%.6f,%.6f\n",
           out[0], out[1], out[2], out[3]);

    int sane =
        out[0] >= 0.0f && out[0] <= 1.0f &&
        out[1] >= 0.0f && out[1] <= 1.0f &&
        out[2] >= 0.0f && out[2] <= 1.0f &&
        out[3] >= 0.90f && out[3] <= 1.01f;
    printf("yv12_sample.output_sane=%s\n", sane ? "PASS" : "FAIL");

    if (!sane) {
        printf("YV12_GPU_SAMPLE_STATUS=FAIL\n");
        ret = 46;
    } else if (write_ref_path) {
        int wr = write_reference(write_ref_path, out);
        printf("yv12_sample.reference_write=%s\n", wr == 0 ? "PASS" : "FAIL");
        printf("YV12_GPU_SAMPLE_STATUS=%s\n", wr == 0 ? "PASS" : "FAIL");
        ret = wr == 0 ? 0 : 47;
    } else if (compare_ref_path) {
        float ref[4] = {0};
        if (read_reference(compare_ref_path, ref) != 0) {
            printf("yv12_sample.reference_read=FAIL\n");
            printf("YV12_GPU_SAMPLE_STATUS=FAIL\n");
            ret = 48;
        } else {
            printf("yv12_sample.reference_rgba=%.6f,%.6f,%.6f,%.6f\n",
                   ref[0], ref[1], ref[2], ref[3]);
            float d0 = absf_local(out[0] - ref[0]);
            float d1 = absf_local(out[1] - ref[1]);
            float d2 = absf_local(out[2] - ref[2]);
            float d3 = absf_local(out[3] - ref[3]);
            float max_diff = d0;
            if (d1 > max_diff) max_diff = d1;
            if (d2 > max_diff) max_diff = d2;
            if (d3 > max_diff) max_diff = d3;
            printf("yv12_sample.reference_diff=%.6f,%.6f,%.6f,%.6f\n",
                   d0, d1, d2, d3);
            printf("yv12_sample.reference_max_diff=%.6f\n", max_diff);
            printf("yv12_sample.reference_tolerance=0.030000\n");
            int match = max_diff <= 0.03f;
            printf("yv12_sample.reference_match=%s\n", match ? "PASS" : "FAIL");
            printf("YV12_GPU_SAMPLE_STATUS=%s\n", match ? "PASS" : "FAIL");
            ret = match ? 0 : 49;
        }
    } else {
        printf("yv12_sample.mode=standalone_sanity\n");
        printf("YV12_GPU_SAMPLE_STATUS=PASS\n");
        ret = 0;
    }

cleanup:
    if (device != VK_NULL_HANDLE)
        vkDeviceWaitIdle(device);
    if (mapped && device != VK_NULL_HANDLE && out_mem != VK_NULL_HANDLE)
        vkUnmapMemory(device, out_mem);
    if (fence) vkDestroyFence(device, fence, NULL);
    if (cmd_pool) vkDestroyCommandPool(device, cmd_pool, NULL);
    if (pipeline) vkDestroyPipeline(device, pipeline, NULL);
    if (shader) vkDestroyShaderModule(device, shader, NULL);
    if (pipeline_layout) vkDestroyPipelineLayout(device, pipeline_layout, NULL);
    if (dpool) vkDestroyDescriptorPool(device, dpool, NULL);
    if (dsl) vkDestroyDescriptorSetLayout(device, dsl, NULL);
    if (out_buffer) vkDestroyBuffer(device, out_buffer, NULL);
    if (out_mem) vkFreeMemory(device, out_mem, NULL);
    if (sampler) vkDestroySampler(device, sampler, NULL);
    if (view) vkDestroyImageView(device, view, NULL);
    if (conversion) vkDestroySamplerYcbcrConversion(device, conversion, NULL);
    if (image) vkDestroyImage(device, image, NULL);
    if (image_mem) vkFreeMemory(device, image_mem, NULL);
    if (ahb) AHardwareBuffer_release(ahb);
    if (device) vkDestroyDevice(device, NULL);
    if (instance) vkDestroyInstance(instance, NULL);

    printf("yv12_sample.exit=%d\n", ret);
    printf("========== END YV12 GPU SAMPLE PROBE ==========\n");
    return ret;
}
