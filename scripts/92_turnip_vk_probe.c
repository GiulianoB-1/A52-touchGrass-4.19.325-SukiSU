#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define TEST_BUFFER_SIZE 4096u
#define TEST_PATTERN 0xA5C3197Bu
#define RENDER_WIDTH 64u
#define RENDER_HEIGHT 64u
#define RENDER_BPP 4u
#define RENDER_BUFFER_SIZE ((VkDeviceSize)RENDER_WIDTH * RENDER_HEIGHT * RENDER_BPP)

static void print_version(const char *key, uint32_t v)
{
    printf("%s=%u.%u.%u\n", key,
           VK_API_VERSION_MAJOR(v),
           VK_API_VERSION_MINOR(v),
           VK_API_VERSION_PATCH(v));
}

static void print_memory_types(VkPhysicalDevice physical)
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(physical, &mp);

    printf("memory_type_count=%u\n", mp.memoryTypeCount);
    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i) {
        printf("memory_type[%u].flags=0x%x\n",
               i, mp.memoryTypes[i].propertyFlags);
        printf("memory_type[%u].heap=%u\n",
               i, mp.memoryTypes[i].heapIndex);
    }
}

static int choose_memory_type(VkPhysicalDevice physical,
                              uint32_t type_bits,
                              VkMemoryPropertyFlags required,
                              VkMemoryPropertyFlags preferred,
                              uint32_t *type_index,
                              VkMemoryPropertyFlags *chosen_flags)
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(physical, &mp);

    int coherent_fallback = -1;
    int visible_fallback = -1;

    /*
     * Prefer HOST_CACHED + HOST_COHERENT where the driver exposes it.
     * The stock Qualcomm driver selected flags=0xf on this device, while
     * Turnip's first matching coherent type is flags=0x7.  Prefer the
     * cached coherent type for an apples-to-apples CPU readback test.
     */
    VkMemoryPropertyFlags strongest =
        required |
        VK_MEMORY_PROPERTY_HOST_COHERENT_BIT |
        VK_MEMORY_PROPERTY_HOST_CACHED_BIT;

    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i) {
        if (!(type_bits & (1u << i)))
            continue;

        VkMemoryPropertyFlags flags = mp.memoryTypes[i].propertyFlags;
        if ((flags & strongest) == strongest) {
            *type_index = i;
            *chosen_flags = flags;
            return 0;
        }

        if ((flags & required) == required &&
            (flags & preferred) == preferred &&
            coherent_fallback < 0)
            coherent_fallback = (int)i;

        if ((flags & required) == required && visible_fallback < 0)
            visible_fallback = (int)i;
    }

    if (coherent_fallback >= 0) {
        *type_index = (uint32_t)coherent_fallback;
        *chosen_flags = mp.memoryTypes[coherent_fallback].propertyFlags;
        return 0;
    }

    if (visible_fallback >= 0) {
        *type_index = (uint32_t)visible_fallback;
        *chosen_flags = mp.memoryTypes[visible_fallback].propertyFlags;
        return 0;
    }

    return -1;
}

static int run_noop_submit_probe(VkDevice device,
                                 VkQueue queue,
                                 uint32_t queue_family)
{
    VkResult r;

    VkCommandPoolCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT,
        .queueFamilyIndex = queue_family,
    };

    VkCommandPool pool = VK_NULL_HANDLE;
    r = vkCreateCommandPool(device, &cpci, NULL, &pool);
    printf("noop_vkCreateCommandPool_result=%d\n", r);
    if (r != VK_SUCCESS)
        return 70;

    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };

    VkCommandBuffer command = VK_NULL_HANDLE;
    r = vkAllocateCommandBuffers(device, &cbai, &command);
    printf("noop_vkAllocateCommandBuffers_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        return 71;
    }

    VkCommandBufferBeginInfo cbbi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };

    r = vkBeginCommandBuffer(command, &cbbi);
    printf("noop_vkBeginCommandBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        return 72;
    }

    r = vkEndCommandBuffer(command);
    printf("noop_vkEndCommandBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        return 73;
    }

    VkFenceCreateInfo fci = {
        .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
    };

    VkFence fence = VK_NULL_HANDLE;
    r = vkCreateFence(device, &fci, NULL, &fence);
    printf("noop_vkCreateFence_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        return 74;
    }

    VkSubmitInfo si = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command,
    };

    printf("noop_before_vkQueueSubmit=1\n");
    fflush(stdout);
    r = vkQueueSubmit(queue, 1, &si, fence);
    printf("noop_vkQueueSubmit_result=%d\n", r);
    fflush(stdout);
    if (r != VK_SUCCESS) {
        vkDestroyFence(device, fence, NULL);
        vkDestroyCommandPool(device, pool, NULL);
        return 75;
    }

    printf("noop_before_vkWaitForFences=1\n");
    fflush(stdout);
    r = vkWaitForFences(device, 1, &fence, VK_TRUE, 5000000000ULL);
    printf("noop_vkWaitForFences_result=%d\n", r);
    fflush(stdout);

    vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, pool, NULL);

    if (r != VK_SUCCESS)
        return 76;

    printf("noop_submit_status=PASS\n");
    fflush(stdout);
    return 0;
}

static int choose_image_memory_type(VkPhysicalDevice physical,
                                    uint32_t type_bits,
                                    uint32_t *type_index)
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(physical, &mp);

    int fallback = -1;
    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i) {
        if (!(type_bits & (1u << i)))
            continue;

        if (fallback < 0)
            fallback = (int)i;

        if (mp.memoryTypes[i].propertyFlags &
            VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) {
            *type_index = i;
            return 0;
        }
    }

    if (fallback >= 0) {
        *type_index = (uint32_t)fallback;
        return 0;
    }

    return -1;
}

static int run_offscreen_render_probe(VkPhysicalDevice physical,
                                      VkDevice device,
                                      VkQueue queue,
                                      uint32_t queue_family)
{
    VkResult r;
    int rc = 0;
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory image_memory = VK_NULL_HANDLE;
    VkImageView image_view = VK_NULL_HANDLE;
    VkBuffer readback = VK_NULL_HANDLE;
    VkDeviceMemory readback_memory = VK_NULL_HANDLE;
    void *mapped = NULL;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;

    printf("=== OFFSCREEN DYNAMIC RENDERING ===\n");
    printf("render_extent=%ux%u\n", RENDER_WIDTH, RENDER_HEIGHT);
    printf("render_format=VK_FORMAT_R8G8B8A8_UNORM\n");
    printf("render_expected_rgba=ff00ffff\n");

    VkFormatProperties fp;
    vkGetPhysicalDeviceFormatProperties(
        physical, VK_FORMAT_R8G8B8A8_UNORM, &fp);
    printf("render_optimal_tiling_features=0x%x\n",
           fp.optimalTilingFeatures);

    const VkFormatFeatureFlags required_features =
        VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT |
        VK_FORMAT_FEATURE_TRANSFER_SRC_BIT;
    if ((fp.optimalTilingFeatures & required_features) != required_features) {
        printf("offscreen_render_error=format_features\n");
        return 80;
    }

    VkImageCreateInfo ici = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D,
        .format = VK_FORMAT_R8G8B8A8_UNORM,
        .extent = { RENDER_WIDTH, RENDER_HEIGHT, 1 },
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
                 VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };

    r = vkCreateImage(device, &ici, NULL, &image);
    printf("render_vkCreateImage_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 81;
        goto cleanup;
    }

    VkMemoryRequirements image_req;
    vkGetImageMemoryRequirements(device, image, &image_req);
    printf("render_image_memory_size=%llu\n",
           (unsigned long long)image_req.size);
    printf("render_image_memory_type_bits=0x%x\n",
           image_req.memoryTypeBits);

    uint32_t image_type = 0;
    if (choose_image_memory_type(physical, image_req.memoryTypeBits,
                                 &image_type) != 0) {
        printf("render_image_memory_type=NONE\n");
        rc = 82;
        goto cleanup;
    }
    printf("render_image_memory_type=%u\n", image_type);

    VkMemoryAllocateInfo image_mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = image_req.size,
        .memoryTypeIndex = image_type,
    };
    r = vkAllocateMemory(device, &image_mai, NULL, &image_memory);
    printf("render_vkAllocateImageMemory_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 83;
        goto cleanup;
    }

    r = vkBindImageMemory(device, image, image_memory, 0);
    printf("render_vkBindImageMemory_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 84;
        goto cleanup;
    }

    VkImageViewCreateInfo ivci = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
        .image = image,
        .viewType = VK_IMAGE_VIEW_TYPE_2D,
        .format = VK_FORMAT_R8G8B8A8_UNORM,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    r = vkCreateImageView(device, &ivci, NULL, &image_view);
    printf("render_vkCreateImageView_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 85;
        goto cleanup;
    }

    VkBufferCreateInfo bci = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = RENDER_BUFFER_SIZE,
        .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    r = vkCreateBuffer(device, &bci, NULL, &readback);
    printf("render_vkCreateReadbackBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 86;
        goto cleanup;
    }

    VkMemoryRequirements read_req;
    vkGetBufferMemoryRequirements(device, readback, &read_req);
    printf("render_readback_memory_size=%llu\n",
           (unsigned long long)read_req.size);
    printf("render_readback_memory_type_bits=0x%x\n",
           read_req.memoryTypeBits);

    uint32_t read_type = 0;
    VkMemoryPropertyFlags read_flags = 0;
    if (choose_memory_type(physical, read_req.memoryTypeBits,
                           VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT,
                           VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                           &read_type, &read_flags) != 0) {
        printf("render_readback_memory_type=NONE\n");
        rc = 87;
        goto cleanup;
    }
    printf("render_readback_memory_type=%u\n", read_type);
    printf("render_readback_memory_flags=0x%x\n", read_flags);

    VkMemoryAllocateInfo read_mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = read_req.size,
        .memoryTypeIndex = read_type,
    };
    r = vkAllocateMemory(device, &read_mai, NULL, &readback_memory);
    printf("render_vkAllocateReadbackMemory_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 88;
        goto cleanup;
    }

    r = vkBindBufferMemory(device, readback, readback_memory, 0);
    printf("render_vkBindReadbackMemory_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 89;
        goto cleanup;
    }

    r = vkMapMemory(device, readback_memory, 0, VK_WHOLE_SIZE, 0, &mapped);
    printf("render_vkMapReadbackMemory_result=%d\n", r);
    if (r != VK_SUCCESS || mapped == NULL) {
        rc = 90;
        goto cleanup;
    }
    memset(mapped, 0x5a, (size_t)RENDER_BUFFER_SIZE);

    VkCommandPoolCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT,
        .queueFamilyIndex = queue_family,
    };
    r = vkCreateCommandPool(device, &cpci, NULL, &pool);
    printf("render_vkCreateCommandPool_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 91;
        goto cleanup;
    }

    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };
    VkCommandBuffer command = VK_NULL_HANDLE;
    r = vkAllocateCommandBuffers(device, &cbai, &command);
    printf("render_vkAllocateCommandBuffers_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 92;
        goto cleanup;
    }

    VkCommandBufferBeginInfo cbbi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };
    r = vkBeginCommandBuffer(command, &cbbi);
    printf("render_vkBeginCommandBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 93;
        goto cleanup;
    }

    VkImageMemoryBarrier to_color = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcAccessMask = 0,
        .dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED,
        .newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    vkCmdPipelineBarrier(command,
                         VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                         VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                         0, 0, NULL, 0, NULL, 1, &to_color);

    PFN_vkCmdBeginRendering pfn_begin =
        (PFN_vkCmdBeginRendering)vkGetDeviceProcAddr(
            device, "vkCmdBeginRendering");
    PFN_vkCmdEndRendering pfn_end =
        (PFN_vkCmdEndRendering)vkGetDeviceProcAddr(
            device, "vkCmdEndRendering");
    printf("render_vkCmdBeginRendering_ptr=%s\n",
           pfn_begin ? "OK" : "NULL");
    printf("render_vkCmdEndRendering_ptr=%s\n",
           pfn_end ? "OK" : "NULL");
    if (!pfn_begin || !pfn_end) {
        rc = 94;
        goto cleanup;
    }

    VkRenderingAttachmentInfo color_attachment = {
        .sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO,
        .imageView = image_view,
        .imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
        .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR,
        .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
        .clearValue = {
            .color = { .float32 = { 1.0f, 0.0f, 1.0f, 1.0f } },
        },
    };
    VkRenderingInfo rendering = {
        .sType = VK_STRUCTURE_TYPE_RENDERING_INFO,
        .renderArea = {
            .offset = { 0, 0 },
            .extent = { RENDER_WIDTH, RENDER_HEIGHT },
        },
        .layerCount = 1,
        .colorAttachmentCount = 1,
        .pColorAttachments = &color_attachment,
    };

    printf("render_before_vkCmdBeginRendering=1\n");
    pfn_begin(command, &rendering);
    pfn_end(command);
    printf("render_after_vkCmdEndRendering=1\n");

    VkImageMemoryBarrier to_transfer = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL,
        .newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = image,
        .subresourceRange = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .baseMipLevel = 0,
            .levelCount = 1,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
    };
    vkCmdPipelineBarrier(command,
                         VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                         VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, NULL, 0, NULL, 1, &to_transfer);

    VkBufferImageCopy copy = {
        .bufferOffset = 0,
        .bufferRowLength = 0,
        .bufferImageHeight = 0,
        .imageSubresource = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT,
            .mipLevel = 0,
            .baseArrayLayer = 0,
            .layerCount = 1,
        },
        .imageOffset = { 0, 0, 0 },
        .imageExtent = { RENDER_WIDTH, RENDER_HEIGHT, 1 },
    };
    vkCmdCopyImageToBuffer(command, image,
                           VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
                           readback, 1, &copy);

    VkBufferMemoryBarrier to_host = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = readback,
        .offset = 0,
        .size = VK_WHOLE_SIZE,
    };
    vkCmdPipelineBarrier(command,
                         VK_PIPELINE_STAGE_TRANSFER_BIT,
                         VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, NULL, 1, &to_host, 0, NULL);

    r = vkEndCommandBuffer(command);
    printf("render_vkEndCommandBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 95;
        goto cleanup;
    }

    VkFenceCreateInfo fci = {
        .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
    };
    r = vkCreateFence(device, &fci, NULL, &fence);
    printf("render_vkCreateFence_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 96;
        goto cleanup;
    }

    VkSubmitInfo si = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command,
    };

    printf("render_before_vkQueueSubmit=1\n");
    r = vkQueueSubmit(queue, 1, &si, fence);
    printf("render_vkQueueSubmit_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 97;
        goto cleanup;
    }

    printf("render_before_vkWaitForFences=1\n");
    r = vkWaitForFences(device, 1, &fence, VK_TRUE, 5000000000ULL);
    printf("render_vkWaitForFences_result=%d\n", r);
    if (r != VK_SUCCESS) {
        rc = 98;
        goto cleanup;
    }

    if (!(read_flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
        VkMappedMemoryRange range = {
            .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
            .memory = readback_memory,
            .offset = 0,
            .size = VK_WHOLE_SIZE,
        };
        r = vkInvalidateMappedMemoryRanges(device, 1, &range);
        printf("render_vkInvalidateMappedMemoryRanges_result=%d\n", r);
        if (r != VK_SUCCESS) {
            rc = 99;
            goto cleanup;
        }
    } else {
        printf("render_vkInvalidateMappedMemoryRanges_result=SKIP_COHERENT\n");
    }

    const uint8_t *pixels = (const uint8_t *)mapped;
    uint32_t mismatches = 0;
    const uint32_t pixel_count = RENDER_WIDTH * RENDER_HEIGHT;
    printf("render_first_pixel=%02x%02x%02x%02x\n",
           pixels[0], pixels[1], pixels[2], pixels[3]);

    for (uint32_t i = 0; i < pixel_count; ++i) {
        const uint8_t *p = pixels + i * 4u;
        if (p[0] != 0xff || p[1] != 0x00 ||
            p[2] != 0xff || p[3] != 0xff) {
            if (mismatches < 8) {
                printf("render_mismatch[%u]=%02x%02x%02x%02x\n",
                       i, p[0], p[1], p[2], p[3]);
            }
            ++mismatches;
        }
    }

    printf("render_verify_pixels=%u\n", pixel_count);
    printf("render_verify_mismatches=%u\n", mismatches);
    if (mismatches != 0) {
        rc = 100;
        goto cleanup;
    }

    printf("offscreen_render_status=PASS\n");

cleanup:
    if (fence != VK_NULL_HANDLE)
        vkDestroyFence(device, fence, NULL);
    if (pool != VK_NULL_HANDLE)
        vkDestroyCommandPool(device, pool, NULL);
    if (mapped != NULL)
        vkUnmapMemory(device, readback_memory);
    if (readback != VK_NULL_HANDLE)
        vkDestroyBuffer(device, readback, NULL);
    if (readback_memory != VK_NULL_HANDLE)
        vkFreeMemory(device, readback_memory, NULL);
    if (image_view != VK_NULL_HANDLE)
        vkDestroyImageView(device, image_view, NULL);
    if (image != VK_NULL_HANDLE)
        vkDestroyImage(device, image, NULL);
    if (image_memory != VK_NULL_HANDLE)
        vkFreeMemory(device, image_memory, NULL);

    if (rc != 0)
        printf("offscreen_render_status=FAIL\n");
    printf("offscreen_render_exit=%d\n", rc);
    return rc;
}

static int run_submit_probe(VkPhysicalDevice physical)
{
    VkResult r;
    uint32_t queue_count = 0;
    uint32_t queue_family = UINT32_MAX;

    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, NULL);
    printf("queue_family_count=%u\n", queue_count);
    if (queue_count == 0)
        return 40;

    VkQueueFamilyProperties *queues = calloc(queue_count, sizeof(*queues));
    if (!queues)
        return 41;

    vkGetPhysicalDeviceQueueFamilyProperties(physical, &queue_count, queues);

    for (uint32_t i = 0; i < queue_count; ++i) {
        printf("queue[%u].flags=0x%x\n", i, queues[i].queueFlags);
        printf("queue[%u].count=%u\n", i, queues[i].queueCount);

        if (queue_family == UINT32_MAX && queues[i].queueCount > 0 &&
            (queues[i].queueFlags &
             (VK_QUEUE_TRANSFER_BIT | VK_QUEUE_GRAPHICS_BIT |
              VK_QUEUE_COMPUTE_BIT)))
            queue_family = i;
    }

    free(queues);

    if (queue_family == UINT32_MAX) {
        printf("submit_queue_family=NONE\n");
        return 42;
    }

    printf("submit_queue_family=%u\n", queue_family);

    const float priority = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = queue_family,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };

    VkPhysicalDeviceProperties submit_props;
    vkGetPhysicalDeviceProperties(physical, &submit_props);

    VkPhysicalDeviceVulkan14Features v14_features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES,
    };
    VkPhysicalDeviceDynamicRenderingFeatures dynamic_rendering = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DYNAMIC_RENDERING_FEATURES,
        .pNext = &v14_features,
    };
    VkPhysicalDeviceFeatures2 features2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &dynamic_rendering,
    };
    vkGetPhysicalDeviceFeatures2(physical, &features2);

    int dynamic_rendering_supported =
        submit_props.apiVersion >= VK_API_VERSION_1_3 &&
        dynamic_rendering.dynamicRendering == VK_TRUE;
    int vulkan14_supported =
        submit_props.apiVersion >= VK_API_VERSION_1_4;

    printf("dynamic_rendering_feature=%u\n",
           dynamic_rendering.dynamicRendering);
    printf("dynamic_rendering_probe_supported=%d\n",
           dynamic_rendering_supported);
    printf("vulkan14_enable_supported=%d\n", vulkan14_supported);
    if (vulkan14_supported) {
        printf("vulkan14_enable.maintenance5=%u\n", v14_features.maintenance5);
        printf("vulkan14_enable.maintenance6=%u\n", v14_features.maintenance6);
        printf("vulkan14_enable.dynamicRenderingLocalRead=%u\n",
               v14_features.dynamicRenderingLocalRead);
        printf("vulkan14_enable.hostImageCopy=%u\n", v14_features.hostImageCopy);
        printf("vulkan14_enable.pushDescriptor=%u\n", v14_features.pushDescriptor);
        printf("vulkan14_enable.pipelineRobustness=%u\n",
               v14_features.pipelineRobustness);
    }

    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .pNext = dynamic_rendering_supported
                    ? &dynamic_rendering
                    : (vulkan14_supported ? &v14_features : NULL),
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
    };

    VkDevice device = VK_NULL_HANDLE;
    r = vkCreateDevice(physical, &dci, NULL, &device);
    printf("vkCreateDevice_result=%d\n", r);
    if (r != VK_SUCCESS)
        return 43;

    VkQueue queue = VK_NULL_HANDLE;
    vkGetDeviceQueue(device, queue_family, 0, &queue);
    if (queue == VK_NULL_HANDLE) {
        printf("vkGetDeviceQueue_result=NULL\n");
        vkDestroyDevice(device, NULL);
        return 44;
    }
    printf("vkGetDeviceQueue_result=OK\n");

    if (vulkan14_supported) {
        const char *v14_dispatch_names[] = {
            "vkCmdPushDescriptorSet",
            "vkCmdPushDescriptorSetKHR",
            "vkCmdBindIndexBuffer2",
            "vkCmdBindIndexBuffer2KHR",
            "vkGetRenderingAreaGranularity",
            "vkGetRenderingAreaGranularityKHR",
            "vkCmdBindDescriptorSets2",
            "vkCmdBindDescriptorSets2KHR",
            "vkCmdPushConstants2",
            "vkCmdPushConstants2KHR",
            "vkCopyMemoryToImage",
            "vkCopyMemoryToImageEXT",
            "vkCopyImageToMemory",
            "vkCopyImageToMemoryEXT",
            "vkTransitionImageLayout",
            "vkTransitionImageLayoutEXT",
        };
        unsigned v14_dispatch_present = 0;
        const unsigned v14_dispatch_count =
            sizeof(v14_dispatch_names) / sizeof(v14_dispatch_names[0]);

        for (unsigned i = 0; i < v14_dispatch_count; ++i) {
            PFN_vkVoidFunction fn =
                vkGetDeviceProcAddr(device, v14_dispatch_names[i]);
            printf("vulkan14_dispatch.%s=%s\n",
                   v14_dispatch_names[i], fn ? "PRESENT" : "MISSING");
            if (fn)
                v14_dispatch_present++;
        }

        printf("vulkan14_dispatch_present=%u/%u\n",
               v14_dispatch_present, v14_dispatch_count);
        printf("vulkan14_feature_enable_status=PASS\n");
    }

    printf("=== NO-OP GPU SUBMISSION ===\n");
    fflush(stdout);
    int noop_rc = run_noop_submit_probe(device, queue, queue_family);
    printf("noop_submit_exit=%d\n", noop_rc);
    fflush(stdout);
    if (noop_rc != 0) {
        vkDestroyDevice(device, NULL);
        return noop_rc;
    }

    printf("=== BUFFER FILL GPU SUBMISSION ===\n");
    fflush(stdout);

    VkBufferCreateInfo bci = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = TEST_BUFFER_SIZE,
        .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT |
                 VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };

    VkBuffer buffer = VK_NULL_HANDLE;
    r = vkCreateBuffer(device, &bci, NULL, &buffer);
    printf("vkCreateBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyDevice(device, NULL);
        return 45;
    }

    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, buffer, &req);
    print_memory_types(physical);
    printf("buffer_memory_size=%llu\n",
           (unsigned long long)req.size);
    printf("buffer_memory_type_bits=0x%x\n", req.memoryTypeBits);

    uint32_t memory_type = 0;
    VkMemoryPropertyFlags memory_flags = 0;
    if (choose_memory_type(physical, req.memoryTypeBits,
                           VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT,
                           VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
                           &memory_type, &memory_flags) != 0) {
        printf("host_visible_memory_type=NONE\n");
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 46;
    }

    printf("host_visible_memory_type=%u\n", memory_type);
    printf("host_visible_memory_flags=0x%x\n", memory_flags);

    VkMemoryAllocateInfo mai = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
        .allocationSize = req.size,
        .memoryTypeIndex = memory_type,
    };

    VkDeviceMemory memory = VK_NULL_HANDLE;
    r = vkAllocateMemory(device, &mai, NULL, &memory);
    printf("vkAllocateMemory_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 47;
    }

    r = vkBindBufferMemory(device, buffer, memory, 0);
    printf("vkBindBufferMemory_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 48;
    }

    /*
     * Map before submitting GPU work.  This separates mmap viability from
     * post-submit CPU readback and avoids introducing a fresh KGSL mmap only
     * after the GPU has already written the allocation.
     */
    void *mapped = NULL;
    printf("pre_submit_vkMapMemory_begin=1\n");
    r = vkMapMemory(device, memory, 0, VK_WHOLE_SIZE, 0, &mapped);
    printf("pre_submit_vkMapMemory_result=%d\n", r);
    printf("pre_submit_mapped_nonnull=%d\n", mapped != NULL);
    if (r != VK_SUCCESS || !mapped) {
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 59;
    }

    ((volatile uint32_t *)mapped)[0] = 0x13579BDFu;
    printf("pre_submit_cpu_sentinel_write=PASS\n");

    VkCommandPoolCreateInfo cpci = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
        .flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT,
        .queueFamilyIndex = queue_family,
    };

    VkCommandPool pool = VK_NULL_HANDLE;
    r = vkCreateCommandPool(device, &cpci, NULL, &pool);
    printf("vkCreateCommandPool_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 49;
    }

    VkCommandBufferAllocateInfo cbai = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
        .commandPool = pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
        .commandBufferCount = 1,
    };

    VkCommandBuffer command = VK_NULL_HANDLE;
    r = vkAllocateCommandBuffers(device, &cbai, &command);
    printf("vkAllocateCommandBuffers_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 50;
    }

    VkCommandBufferBeginInfo cbbi = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
        .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
    };

    r = vkBeginCommandBuffer(command, &cbbi);
    printf("vkBeginCommandBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 51;
    }

    vkCmdFillBuffer(command, buffer, 0, TEST_BUFFER_SIZE, TEST_PATTERN);

    r = vkEndCommandBuffer(command);
    printf("vkEndCommandBuffer_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 52;
    }

    VkFenceCreateInfo fci = {
        .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO,
    };

    VkFence fence = VK_NULL_HANDLE;
    r = vkCreateFence(device, &fci, NULL, &fence);
    printf("vkCreateFence_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 53;
    }

    VkSubmitInfo si = {
        .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
        .commandBufferCount = 1,
        .pCommandBuffers = &command,
    };

    printf("fill_before_vkQueueSubmit=1\n");
    fflush(stdout);
    r = vkQueueSubmit(queue, 1, &si, fence);
    printf("vkQueueSubmit_result=%d\n", r);
    fflush(stdout);
    if (r != VK_SUCCESS) {
        vkDestroyFence(device, fence, NULL);
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 54;
    }

    printf("fill_before_vkWaitForFences=1\n");
    fflush(stdout);
    r = vkWaitForFences(device, 1, &fence, VK_TRUE, 5000000000ULL);
    printf("vkWaitForFences_result=%d\n", r);
    fflush(stdout);
    if (r != VK_SUCCESS) {
        vkDeviceWaitIdle(device);
        vkDestroyFence(device, fence, NULL);
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 55;
    }

    printf("post_submit_reuse_existing_mapping=1\n");
    if (!(memory_flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
        VkMappedMemoryRange range = {
            .sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE,
            .memory = memory,
            .offset = 0,
            .size = VK_WHOLE_SIZE,
        };
        r = vkInvalidateMappedMemoryRanges(device, 1, &range);
        printf("vkInvalidateMappedMemoryRanges_result=%d\n", r);
        if (r != VK_SUCCESS) {
            vkUnmapMemory(device, memory);
            vkDestroyFence(device, fence, NULL);
            vkDestroyCommandPool(device, pool, NULL);
            vkFreeMemory(device, memory, NULL);
            vkDestroyBuffer(device, buffer, NULL);
            vkDestroyDevice(device, NULL);
            return 57;
        }
    } else {
        printf("vkInvalidateMappedMemoryRanges_result=SKIP_COHERENT\n");
    }

    uint32_t mismatches = 0;
    uint32_t *words = (uint32_t *)mapped;
    const uint32_t word_count = TEST_BUFFER_SIZE / sizeof(uint32_t);

    for (uint32_t i = 0; i < word_count; ++i) {
        if (words[i] != TEST_PATTERN) {
            if (mismatches < 8)
                printf("verify_mismatch[%u]=0x%08x\n", i, words[i]);
            ++mismatches;
        }
    }

    printf("verify_pattern=0x%08x\n", TEST_PATTERN);
    printf("verify_words=%u\n", word_count);
    printf("verify_mismatches=%u\n", mismatches);

    vkUnmapMemory(device, memory);
    vkDestroyFence(device, fence, NULL);
    vkDestroyCommandPool(device, pool, NULL);
    vkFreeMemory(device, memory, NULL);
    vkDestroyBuffer(device, buffer, NULL);

    if (mismatches != 0) {
        vkDestroyDevice(device, NULL);
        return 58;
    }

    printf("gpu_submit_status=PASS\n");

    if (dynamic_rendering_supported) {
        int render_rc = run_offscreen_render_probe(
            physical, device, queue, queue_family);
        if (render_rc != 0) {
            vkDestroyDevice(device, NULL);
            return render_rc;
        }
    } else {
        printf("offscreen_render_status=SKIP_UNSUPPORTED\n");
        printf("offscreen_render_exit=0\n");
    }

    vkDestroyDevice(device, NULL);
    return 0;
}

int main(void)
{
    /* Preserve the exact last successful milestone if Turnip hangs, crashes,
     * or the GPU resets during a submission. */
    setvbuf(stdout, NULL, _IONBF, 0);

    uint32_t loader_version = VK_API_VERSION_1_0;
    PFN_vkEnumerateInstanceVersion enumerate_instance_version =
        (PFN_vkEnumerateInstanceVersion)vkGetInstanceProcAddr(
            NULL, "vkEnumerateInstanceVersion");

    if (enumerate_instance_version) {
        VkResult vr = enumerate_instance_version(&loader_version);
        if (vr != VK_SUCCESS) {
            printf("vkEnumerateInstanceVersion_result=%d\n", vr);
            return 10;
        }
    }

    print_version("loader_instance_version", loader_version);

    uint32_t requested = loader_version < VK_API_VERSION_1_4
        ? loader_version : VK_API_VERSION_1_4;
    print_version("requested_instance_version", requested);

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "touchGrass Turnip A619 submit probe",
        .applicationVersion = VK_MAKE_VERSION(2, 0, 0),
        .pEngineName = "touchGrass",
        .engineVersion = VK_MAKE_VERSION(1, 0, 0),
        .apiVersion = requested,
    };

    VkInstanceCreateInfo ci = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        .pApplicationInfo = &app,
    };

    VkInstance instance = VK_NULL_HANDLE;
    VkResult r = vkCreateInstance(&ci, NULL, &instance);
    printf("vkCreateInstance_result=%d\n", r);
    if (r != VK_SUCCESS)
        return 20;

    uint32_t count = 0;
    r = vkEnumeratePhysicalDevices(instance, &count, NULL);
    printf("vkEnumeratePhysicalDevices_result=%d\n", r);
    printf("physical_device_count=%u\n", count);
    if (r != VK_SUCCESS || count == 0) {
        vkDestroyInstance(instance, NULL);
        return 30;
    }

    VkPhysicalDevice *devices = calloc(count, sizeof(*devices));
    if (!devices) {
        vkDestroyInstance(instance, NULL);
        return 31;
    }

    r = vkEnumeratePhysicalDevices(instance, &count, devices);
    if (r != VK_SUCCESS) {
        free(devices);
        vkDestroyInstance(instance, NULL);
        return 32;
    }

    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(devices[i], &p);

        printf("device[%u].name=%s\n", i, p.deviceName);
        printf("device[%u].vendor_id=0x%04x\n", i, p.vendorID);
        printf("device[%u].device_id=0x%04x\n", i, p.deviceID);
        print_version("device_api_version", p.apiVersion);
        printf("device[%u].driver_version_raw=%u\n", i, p.driverVersion);

#ifdef VK_VERSION_1_4
        if (p.apiVersion >= VK_API_VERSION_1_4) {
            VkPhysicalDeviceVulkan14Features v14_features = {
                .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_FEATURES,
            };
            VkPhysicalDeviceFeatures2 v14_features2 = {
                .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
                .pNext = &v14_features,
            };
            vkGetPhysicalDeviceFeatures2(devices[i], &v14_features2);

            VkPhysicalDeviceVulkan14Properties v14_props = {
                .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_4_PROPERTIES,
            };
            VkPhysicalDeviceProperties2 v14_props2 = {
                .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2,
                .pNext = &v14_props,
            };
            vkGetPhysicalDeviceProperties2(devices[i], &v14_props2);

            printf("device[%u].vulkan14_query=PASS\n", i);
            printf("device[%u].vulkan14.maintenance5=%u\n",
                   i, v14_features.maintenance5);
            printf("device[%u].vulkan14.maintenance6=%u\n",
                   i, v14_features.maintenance6);
            printf("device[%u].vulkan14.dynamicRenderingLocalRead=%u\n",
                   i, v14_features.dynamicRenderingLocalRead);
            printf("device[%u].vulkan14.hostImageCopy=%u\n",
                   i, v14_features.hostImageCopy);
            printf("device[%u].vulkan14.pushDescriptor=%u\n",
                   i, v14_features.pushDescriptor);
            printf("device[%u].vulkan14.pipelineRobustness=%u\n",
                   i, v14_features.pipelineRobustness);
            printf("device[%u].vulkan14.maxPushDescriptors=%u\n",
                   i, v14_props.maxPushDescriptors);
            printf("device[%u].vulkan14.maxVertexAttribDivisor=%u\n",
                   i, v14_props.maxVertexAttribDivisor);
            printf("device[%u].vulkan14.identicalMemoryTypeRequirements=%u\n",
                   i, v14_props.identicalMemoryTypeRequirements);
        } else {
            printf("device[%u].vulkan14_query=FAIL_API_TOO_LOW\n", i);
        }
#else
        printf("device[%u].vulkan14_query=FAIL_HEADERS_TOO_OLD\n", i);
#endif

        uint32_t ext_count = 0;
        VkResult er = vkEnumerateDeviceExtensionProperties(
            devices[i], NULL, &ext_count, NULL);
        printf("device[%u].extension_query_result=%d\n", i, er);
        printf("device[%u].extension_count=%u\n", i, ext_count);

        if (er == VK_SUCCESS && ext_count > 0) {
            VkExtensionProperties *exts =
                calloc(ext_count, sizeof(*exts));
            if (exts) {
                er = vkEnumerateDeviceExtensionProperties(
                    devices[i], NULL, &ext_count, exts);
                int has_swapchain = 0;
                int has_timeline = 0;
                int has_dynamic_rendering = 0;

                for (uint32_t j = 0;
                     er == VK_SUCCESS && j < ext_count; ++j) {
                    if (!strcmp(exts[j].extensionName,
                                VK_KHR_SWAPCHAIN_EXTENSION_NAME))
                        has_swapchain = 1;
                    if (!strcmp(exts[j].extensionName,
                                "VK_KHR_timeline_semaphore"))
                        has_timeline = 1;
                    if (!strcmp(exts[j].extensionName,
                                "VK_KHR_dynamic_rendering"))
                        has_dynamic_rendering = 1;
                }

                printf("device[%u].has_VK_KHR_swapchain=%d\n",
                       i, has_swapchain);
                printf("device[%u].has_VK_KHR_timeline_semaphore=%d\n",
                       i, has_timeline);
                printf("device[%u].has_VK_KHR_dynamic_rendering=%d\n",
                       i, has_dynamic_rendering);
                free(exts);
            }
        }
    }

    VkPhysicalDeviceProperties primary_props;
    vkGetPhysicalDeviceProperties(devices[0], &primary_props);
    if (primary_props.apiVersion < VK_API_VERSION_1_4) {
        printf("vulkan14_device_api_status=FAIL\n");
        free(devices);
        vkDestroyInstance(instance, NULL);
        return 33;
    }
    printf("vulkan14_device_api_status=PASS\n");

    printf("=== GPU COMMAND SUBMISSION ===\n");
    int submit_rc = run_submit_probe(devices[0]);
    printf("gpu_submit_exit=%d\n", submit_rc);

    free(devices);
    vkDestroyInstance(instance, NULL);

    if (submit_rc != 0) {
        printf("probe_status=FAIL\n");
        return submit_rc;
    }

    printf("probe_status=PASS\n");
    return 0;
}
