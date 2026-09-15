#include <android/hardware_buffer.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>
#include <vulkan/vulkan_android.h>

#ifndef AHARDWAREBUFFER_FORMAT_Y8Cb8Cr8_420
#define AHARDWAREBUFFER_FORMAT_Y8Cb8Cr8_420 0x23
#endif

#ifndef AHARDWAREBUFFER_FORMAT_YV12
#define AHARDWAREBUFFER_FORMAT_YV12 0x32315659u
#endif

#ifndef TOUCHGRASS_QTI_NV12_UBWC
#define TOUCHGRASS_QTI_NV12_UBWC 0x7fa30c06u
#endif

#ifndef TOUCHGRASS_QTI_TP10_UBWC
#define TOUCHGRASS_QTI_TP10_UBWC 0x7fa30c09u
#endif

#define TEST_W 256u
#define TEST_H 256u
#define SF_YV12_W 940u
#define SF_YV12_H 1670u

static const char *vk_result_name(VkResult r)
{
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_EVENT_SET: return "VK_EVENT_SET";
    case VK_EVENT_RESET: return "VK_EVENT_RESET";
    case VK_INCOMPLETE: return "VK_INCOMPLETE";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_MEMORY_MAP_FAILED: return "VK_ERROR_MEMORY_MAP_FAILED";
    case VK_ERROR_LAYER_NOT_PRESENT: return "VK_ERROR_LAYER_NOT_PRESENT";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
    case VK_ERROR_TOO_MANY_OBJECTS: return "VK_ERROR_TOO_MANY_OBJECTS";
    case VK_ERROR_FORMAT_NOT_SUPPORTED: return "VK_ERROR_FORMAT_NOT_SUPPORTED";
    case VK_ERROR_FRAGMENTED_POOL: return "VK_ERROR_FRAGMENTED_POOL";
    case (VkResult)-1000072003: return "VK_ERROR_INVALID_EXTERNAL_HANDLE";
    case (VkResult)-1000158000: return "VK_ERROR_INVALID_DRM_FORMAT_MODIFIER_PLANE_LAYOUT_EXT";
    default: return "VK_RESULT_OTHER";
    }
}

static int has_device_extension(VkPhysicalDevice physical, const char *name)
{
    uint32_t count = 0;
    if (vkEnumerateDeviceExtensionProperties(physical, NULL, &count, NULL) != VK_SUCCESS)
        return 0;

    VkExtensionProperties *exts = calloc(count, sizeof(*exts));
    if (!exts)
        return 0;

    VkResult r = vkEnumerateDeviceExtensionProperties(physical, NULL, &count, exts);
    if (r != VK_SUCCESS) {
        free(exts);
        return 0;
    }

    int found = 0;
    for (uint32_t i = 0; i < count; ++i) {
        if (strcmp(exts[i].extensionName, name) == 0) {
            found = 1;
            break;
        }
    }

    free(exts);
    return found;
}

static int choose_memory_type(uint32_t bits, uint32_t *index)
{
    if (!bits)
        return -1;
    for (uint32_t i = 0; i < 32; ++i) {
        if (bits & (1u << i)) {
            *index = i;
            return 0;
        }
    }
    return -1;
}

struct ahb_case {
    const char *name;
    uint32_t format;
    uint32_t width;
    uint32_t height;
    uint64_t usage;
};

static int run_ahb_case(VkPhysicalDevice physical,
                        VkDevice device,
                        PFN_vkGetAndroidHardwareBufferPropertiesANDROID pGetProps,
                        const struct ahb_case *tc)
{
    (void)physical;

    printf("\n=== AHB CASE %s ===\n", tc->name);
    printf("%s.format_decimal=%u\n", tc->name, tc->format);
    printf("%s.format_hex=0x%08x\n", tc->name, tc->format);
    printf("%s.extent=%ux%u\n", tc->name, tc->width, tc->height);
    printf("%s.usage=0x%" PRIx64 "\n", tc->name, tc->usage);

    AHardwareBuffer_Desc desc = {
        .width = tc->width,
        .height = tc->height,
        .layers = 1,
        .format = tc->format,
        .usage = tc->usage,
        .stride = 0,
        .rfu0 = 0,
        .rfu1 = 0,
    };

#if __ANDROID_API__ >= 29
    int supported = AHardwareBuffer_isSupported(&desc);
    printf("%s.ahb_is_supported=%d\n", tc->name, supported);
#else
    printf("%s.ahb_is_supported=NA\n", tc->name);
#endif

    AHardwareBuffer *ahb = NULL;
    int ahb_rc = AHardwareBuffer_allocate(&desc, &ahb);
    printf("%s.ahb_allocate_result=%d\n", tc->name, ahb_rc);
    if (ahb_rc != 0 || !ahb) {
        printf("%s.status=AHB_ALLOC_FAIL\n", tc->name);
        return 1;
    }

    AHardwareBuffer_Desc got = {0};
    AHardwareBuffer_describe(ahb, &got);
    printf("%s.desc.width=%u\n", tc->name, got.width);
    printf("%s.desc.height=%u\n", tc->name, got.height);
    printf("%s.desc.layers=%u\n", tc->name, got.layers);
    printf("%s.desc.format_decimal=%u\n", tc->name, got.format);
    printf("%s.desc.format_hex=0x%08x\n", tc->name, got.format);
    printf("%s.desc.usage=0x%" PRIx64 "\n", tc->name, got.usage);
    printf("%s.desc.stride=%u\n", tc->name, got.stride);

    VkAndroidHardwareBufferFormatPropertiesANDROID fmt = {
        .sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_FORMAT_PROPERTIES_ANDROID,
    };
    VkAndroidHardwareBufferPropertiesANDROID props = {
        .sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID,
        .pNext = &fmt,
    };

    VkResult r = pGetProps(device, ahb, &props);
    printf("%s.vkGetAndroidHardwareBufferPropertiesANDROID=%d:%s\n",
           tc->name, r, vk_result_name(r));
    if (r != VK_SUCCESS) {
        AHardwareBuffer_release(ahb);
        printf("%s.status=GET_PROPS_FAIL\n", tc->name);
        return 2;
    }

    printf("%s.props.allocationSize=%" PRIu64 "\n",
           tc->name, (uint64_t)props.allocationSize);
    printf("%s.props.memoryTypeBits=0x%08x\n",
           tc->name, props.memoryTypeBits);
    printf("%s.props.format=%d\n", tc->name, fmt.format);
    printf("%s.props.externalFormat=%" PRIu64 "\n",
           tc->name, (uint64_t)fmt.externalFormat);
    printf("%s.props.formatFeatures=0x%08x\n",
           tc->name, fmt.formatFeatures);
    printf("%s.props.ycbcrModel=%d\n", tc->name, fmt.suggestedYcbcrModel);
    printf("%s.props.ycbcrRange=%d\n", tc->name, fmt.suggestedYcbcrRange);
    printf("%s.props.xChromaOffset=%d\n", tc->name, fmt.suggestedXChromaOffset);
    printf("%s.props.yChromaOffset=%d\n", tc->name, fmt.suggestedYChromaOffset);

    VkExternalFormatANDROID external_format = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_FORMAT_ANDROID,
        .externalFormat = fmt.externalFormat,
    };
    VkExternalMemoryImageCreateInfo external_mem = {
        .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
        .pNext = fmt.format == VK_FORMAT_UNDEFINED ? &external_format : NULL,
        .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID,
    };

    VkImageCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .pNext = &external_mem,
        .imageType = VK_IMAGE_TYPE_2D,
        .format = fmt.format,
        .extent = { got.width, got.height, 1 },
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_SAMPLED_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };

    VkImage image = VK_NULL_HANDLE;
    r = vkCreateImage(device, &ici, NULL, &image);
    printf("%s.vkCreateImage=%d:%s\n", tc->name, r, vk_result_name(r));
    if (r != VK_SUCCESS) {
        AHardwareBuffer_release(ahb);
        printf("%s.status=CREATE_IMAGE_FAIL\n", tc->name);
        return 3;
    }

    VkMemoryRequirements mem_req = {0};
    vkGetImageMemoryRequirements(device, image, &mem_req);
    printf("%s.image.memoryRequirements.size=%" PRIu64 "\n",
           tc->name, (uint64_t)mem_req.size);
    printf("%s.image.memoryRequirements.alignment=%" PRIu64 "\n",
           tc->name, (uint64_t)mem_req.alignment);
    printf("%s.image.memoryRequirements.memoryTypeBits=0x%08x\n",
           tc->name, mem_req.memoryTypeBits);

    uint32_t compatible = mem_req.memoryTypeBits & props.memoryTypeBits;
    printf("%s.memory.compatibleTypeBits=0x%08x\n", tc->name, compatible);

    uint32_t memory_type = 0;
    if (choose_memory_type(compatible, &memory_type) != 0) {
        vkDestroyImage(device, image, NULL);
        AHardwareBuffer_release(ahb);
        printf("%s.status=NO_COMPATIBLE_MEMORY_TYPE\n", tc->name);
        return 4;
    }
    printf("%s.memory.selectedType=%u\n", tc->name, memory_type);

    VkImportAndroidHardwareBufferInfoANDROID import_ahb = {
        .sType = VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID,
        .buffer = ahb,
    };
    VkMemoryDedicatedAllocateInfo dedicated = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
        .pNext = &import_ahb,
        .image = image,
        .buffer = VK_NULL_HANDLE,
    };
    VkMemoryAllocateInfo mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .pNext = &dedicated,
        .allocationSize = props.allocationSize,
        .memoryTypeIndex = memory_type,
    };

    VkDeviceMemory memory = VK_NULL_HANDLE;
    r = vkAllocateMemory(device, &mai, NULL, &memory);
    printf("%s.vkAllocateMemory_AHB=%d:%s\n", tc->name, r, vk_result_name(r));
    if (r != VK_SUCCESS) {
        vkDestroyImage(device, image, NULL);
        AHardwareBuffer_release(ahb);
        printf("%s.status=ALLOCATE_IMPORT_MEMORY_FAIL\n", tc->name);
        return 5;
    }

    r = vkBindImageMemory(device, image, memory, 0);
    printf("%s.vkBindImageMemory=%d:%s\n", tc->name, r, vk_result_name(r));
    if (r != VK_SUCCESS) {
        vkDestroyImage(device, image, NULL);
        vkFreeMemory(device, memory, NULL);
        AHardwareBuffer_release(ahb);
        printf("%s.status=BIND_FAIL\n", tc->name);
        return 6;
    }

    if (fmt.format == VK_FORMAT_UNDEFINED && fmt.externalFormat != 0) {
        PFN_vkCreateSamplerYcbcrConversion pCreateYcbcr =
            (PFN_vkCreateSamplerYcbcrConversion)vkGetDeviceProcAddr(
                device, "vkCreateSamplerYcbcrConversion");

        printf("%s.vkCreateSamplerYcbcrConversion_ptr=%s\n",
               tc->name, pCreateYcbcr ? "OK" : "NULL");

        if (pCreateYcbcr) {
            VkExternalFormatANDROID conv_external = {
                .sType = VK_STRUCTURE_TYPE_EXTERNAL_FORMAT_ANDROID,
                .externalFormat = fmt.externalFormat,
            };
            VkSamplerYcbcrConversionCreateInfo ci = {
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
            VkSamplerYcbcrConversion conversion = VK_NULL_HANDLE;
            VkResult cr = pCreateYcbcr(device, &ci, NULL, &conversion);
            printf("%s.vkCreateSamplerYcbcrConversion=%d:%s\n",
                   tc->name, cr, vk_result_name(cr));
            if (cr == VK_SUCCESS) {
                PFN_vkDestroySamplerYcbcrConversion pDestroyYcbcr =
                    (PFN_vkDestroySamplerYcbcrConversion)vkGetDeviceProcAddr(
                        device, "vkDestroySamplerYcbcrConversion");
                if (pDestroyYcbcr)
                    pDestroyYcbcr(device, conversion, NULL);
            }
        }
    } else {
        printf("%s.vkCreateSamplerYcbcrConversion=SKIP_NON_EXTERNAL_FORMAT\n",
               tc->name);
    }

    vkDestroyImage(device, image, NULL);
    vkFreeMemory(device, memory, NULL);
    AHardwareBuffer_release(ahb);

    printf("%s.status=IMPORT_BIND_PASS\n", tc->name);
    return 0;
}

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);

    printf("========== TURNIP/STOCK AHB IMPORT PROBE ==========\n");

    uint32_t loader_version = VK_API_VERSION_1_0;
    PFN_vkEnumerateInstanceVersion pEnumInstanceVersion =
        (PFN_vkEnumerateInstanceVersion)vkGetInstanceProcAddr(
            VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
    if (pEnumInstanceVersion)
        pEnumInstanceVersion(&loader_version);

    printf("loader_instance_version=%u.%u.%u\n",
           VK_API_VERSION_MAJOR(loader_version),
           VK_API_VERSION_MINOR(loader_version),
           VK_API_VERSION_PATCH(loader_version));

    uint32_t request_version =
        loader_version >= VK_API_VERSION_1_1 ? VK_API_VERSION_1_1
                                             : VK_API_VERSION_1_0;

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "touchGrass-AHB-Probe",
        .applicationVersion = 1,
        .pEngineName = "touchGrass",
        .engineVersion = 1,
        .apiVersion = request_version,
    };
    VkInstanceCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };

    VkInstance instance = VK_NULL_HANDLE;
    VkResult r = vkCreateInstance(&ici, NULL, &instance);
    printf("vkCreateInstance=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS)
        return 10;

    uint32_t physical_count = 0;
    r = vkEnumeratePhysicalDevices(instance, &physical_count, NULL);
    printf("vkEnumeratePhysicalDevices_count_result=%d:%s\n",
           r, vk_result_name(r));
    printf("physical_device_count=%u\n", physical_count);
    if (r != VK_SUCCESS || physical_count == 0) {
        vkDestroyInstance(instance, NULL);
        return 11;
    }

    VkPhysicalDevice physical = VK_NULL_HANDLE;
    physical_count = 1;
    r = vkEnumeratePhysicalDevices(instance, &physical_count, &physical);
    if (r != VK_SUCCESS) {
        vkDestroyInstance(instance, NULL);
        return 12;
    }

    VkPhysicalDeviceProperties p = {0};
    vkGetPhysicalDeviceProperties(physical, &p);
    printf("device_name=%s\n", p.deviceName);
    printf("device_api_version=%u.%u.%u\n",
           VK_API_VERSION_MAJOR(p.apiVersion),
           VK_API_VERSION_MINOR(p.apiVersion),
           VK_API_VERSION_PATCH(p.apiVersion));

    const char *ahb_ext = VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME;
    int has_ahb = has_device_extension(physical, ahb_ext);
    printf("has_%s=%d\n", ahb_ext, has_ahb);
    if (!has_ahb) {
        vkDestroyInstance(instance, NULL);
        return 13;
    }

    uint32_t q_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &q_count, NULL);
    VkQueueFamilyProperties *qprops = calloc(q_count, sizeof(*qprops));
    if (!qprops) {
        vkDestroyInstance(instance, NULL);
        return 14;
    }
    vkGetPhysicalDeviceQueueFamilyProperties(physical, &q_count, qprops);

    uint32_t queue_family = UINT32_MAX;
    for (uint32_t i = 0; i < q_count; ++i) {
        if (qprops[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
            queue_family = i;
            break;
        }
    }
    free(qprops);

    if (queue_family == UINT32_MAX) {
        vkDestroyInstance(instance, NULL);
        return 15;
    }

    float priority = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };

    VkPhysicalDeviceSamplerYcbcrConversionFeatures ycbcr = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SAMPLER_YCBCR_CONVERSION_FEATURES,
    };
    VkPhysicalDeviceFeatures2 f2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &ycbcr,
    };
    vkGetPhysicalDeviceFeatures2(physical, &f2);
    printf("samplerYcbcrConversion_feature=%u\n", ycbcr.samplerYcbcrConversion);

    const char *device_exts[] = {
        VK_ANDROID_EXTERNAL_MEMORY_ANDROID_HARDWARE_BUFFER_EXTENSION_NAME,
    };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext = ycbcr.samplerYcbcrConversion ? &ycbcr : NULL,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
        .enabledExtensionCount = 1,
        .ppEnabledExtensionNames = device_exts,
    };

    VkDevice device = VK_NULL_HANDLE;
    r = vkCreateDevice(physical, &dci, NULL, &device);
    printf("vkCreateDevice=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) {
        vkDestroyInstance(instance, NULL);
        return 16;
    }

    PFN_vkGetAndroidHardwareBufferPropertiesANDROID pGetProps =
        (PFN_vkGetAndroidHardwareBufferPropertiesANDROID)vkGetDeviceProcAddr(
            device, "vkGetAndroidHardwareBufferPropertiesANDROID");
    printf("vkGetAndroidHardwareBufferPropertiesANDROID_ptr=%s\n",
           pGetProps ? "OK" : "NULL");
    if (!pGetProps) {
        vkDestroyDevice(device, NULL);
        vkDestroyInstance(instance, NULL);
        return 17;
    }

    const uint64_t sampled_usage =
        AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
        AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY;

    /* Private QTI UBWC video buffers are GPU/compositor allocations. Do not
     * request CPU mapping, because CPU access can force a different physical
     * layout or make the allocation unsupported.
     */
    const uint64_t sampled_gpu_only_usage =
        AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE;

    const struct ahb_case cases[] = {
        {
            .name = "rgba8_256",
            .format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM,
            .width = TEST_W,
            .height = TEST_H,
            .usage = sampled_usage,
        },
        {
            .name = "yuv420_256",
            .format = AHARDWAREBUFFER_FORMAT_Y8Cb8Cr8_420,
            .width = TEST_W,
            .height = TEST_H,
            .usage = sampled_usage,
        },
        {
            .name = "yv12_256",
            .format = AHARDWAREBUFFER_FORMAT_YV12,
            .width = TEST_W,
            .height = TEST_H,
            .usage = sampled_usage,
        },
        {
            .name = "yv12_sf_940x1670",
            .format = AHARDWAREBUFFER_FORMAT_YV12,
            .width = SF_YV12_W,
            .height = SF_YV12_H,
            .usage = sampled_usage,
        },
        {
            .name = "qti_nv12_ubwc_720x1280",
            .format = TOUCHGRASS_QTI_NV12_UBWC,
            .width = 720,
            .height = 1280,
            .usage = sampled_gpu_only_usage,
        },
        {
            .name = "qti_tp10_ubwc_1080x1920",
            .format = TOUCHGRASS_QTI_TP10_UBWC,
            .width = 1080,
            .height = 1920,
            .usage = sampled_gpu_only_usage,
        },
    };

    unsigned pass = 0, fail = 0;
    int target_yv12_sf_rc = -1;
    int target_qti_nv12_ubwc_rc = -1;
    int target_qti_tp10_ubwc_rc = -1;
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        int rc = run_ahb_case(physical, device, pGetProps, &cases[i]);
        if (rc == 0)
            ++pass;
        else
            ++fail;
        if (strcmp(cases[i].name, "yv12_sf_940x1670") == 0)
            target_yv12_sf_rc = rc;
        if (strcmp(cases[i].name, "qti_nv12_ubwc_720x1280") == 0)
            target_qti_nv12_ubwc_rc = rc;
        if (strcmp(cases[i].name, "qti_tp10_ubwc_1080x1920") == 0)
            target_qti_tp10_ubwc_rc = rc;
        printf("%s.case_exit=%d\n", cases[i].name, rc);
    }

    printf("\n=== AHB LIFETIME STRESS RGBA8 ===\n");
    unsigned stress_pass = 0;
    for (unsigned i = 0; i < 32; ++i) {
        struct ahb_case stress = {
            .name = "stress_rgba8",
            .format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM,
            .width = 64,
            .height = 64,
            .usage = sampled_usage,
        };
        int rc = run_ahb_case(physical, device, pGetProps, &stress);
        printf("stress_iteration=%u rc=%d\n", i, rc);
        if (rc != 0)
            break;
        ++stress_pass;
    }
    printf("stress_rgba8_passed=%u/32\n", stress_pass);

    vkDeviceWaitIdle(device);
    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);

    printf("\nAHB_CASES_PASS=%u\n", pass);
    printf("AHB_CASES_FAIL=%u\n", fail);
    printf("target_yv12_sf_940x1670_exit=%d\n", target_yv12_sf_rc);
    if (target_yv12_sf_rc == 0)
        printf("target_yv12_sf_940x1670_status=PASS\n");
    else
        printf("target_yv12_sf_940x1670_status=FAIL\n");
    printf("target_qti_nv12_ubwc_720x1280_exit=%d\n",
           target_qti_nv12_ubwc_rc);
    if (target_qti_nv12_ubwc_rc == 0)
        printf("target_qti_nv12_ubwc_720x1280_status=PASS\n");
    else
        printf("target_qti_nv12_ubwc_720x1280_status=DIAGNOSTIC_FAIL\n");
    printf("target_qti_tp10_ubwc_1080x1920_exit=%d\n",
           target_qti_tp10_ubwc_rc);
    if (target_qti_tp10_ubwc_rc == 0)
        printf("target_qti_tp10_ubwc_1080x1920_status=PASS\n");
    else
        printf("target_qti_tp10_ubwc_1080x1920_status=DIAGNOSTIC_FAIL\n");
    printf("ahb_probe_status=COMPLETE\n");

    /* Keep YV12 as the hard regression gate. The private QTI UBWC cases are
     * diagnostic because some gralloc revisions refuse direct allocation of
     * vendor-private formats even though camera/video producers can supply
     * such buffers. Persistent SurfaceFlinger is the authoritative private
     * QTI UBWC integration test. */
    return target_yv12_sf_rc == 0 ? 0 : 30;
}
