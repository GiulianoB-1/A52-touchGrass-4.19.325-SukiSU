#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

#define TEST_BUFFER_SIZE 4096u
#define TEST_PATTERN 0xA5C3197Bu

static void print_version(const char *key, uint32_t v)
{
    printf("%s=%u.%u.%u\n", key,
           VK_API_VERSION_MAJOR(v),
           VK_API_VERSION_MINOR(v),
           VK_API_VERSION_PATCH(v));
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

    int fallback = -1;

    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i) {
        if (!(type_bits & (1u << i)))
            continue;

        VkMemoryPropertyFlags flags = mp.memoryTypes[i].propertyFlags;
        if ((flags & required) != required)
            continue;

        if ((flags & preferred) == preferred) {
            *type_index = i;
            *chosen_flags = flags;
            return 0;
        }

        if (fallback < 0)
            fallback = (int)i;
    }

    if (fallback >= 0) {
        *type_index = (uint32_t)fallback;
        *chosen_flags = mp.memoryTypes[fallback].propertyFlags;
        return 0;
    }

    return -1;
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

    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
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

    r = vkQueueSubmit(queue, 1, &si, fence);
    printf("vkQueueSubmit_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDestroyFence(device, fence, NULL);
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 54;
    }

    r = vkWaitForFences(device, 1, &fence, VK_TRUE, 5000000000ULL);
    printf("vkWaitForFences_result=%d\n", r);
    if (r != VK_SUCCESS) {
        vkDeviceWaitIdle(device);
        vkDestroyFence(device, fence, NULL);
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 55;
    }

    void *mapped = NULL;
    r = vkMapMemory(device, memory, 0, VK_WHOLE_SIZE, 0, &mapped);
    printf("vkMapMemory_result=%d\n", r);
    if (r != VK_SUCCESS || !mapped) {
        vkDestroyFence(device, fence, NULL);
        vkDestroyCommandPool(device, pool, NULL);
        vkFreeMemory(device, memory, NULL);
        vkDestroyBuffer(device, buffer, NULL);
        vkDestroyDevice(device, NULL);
        return 56;
    }

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
    vkDestroyDevice(device, NULL);

    if (mismatches != 0)
        return 58;

    printf("gpu_submit_status=PASS\n");
    return 0;
}

int main(void)
{
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

    uint32_t requested = loader_version < VK_API_VERSION_1_3
        ? loader_version : VK_API_VERSION_1_3;
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
