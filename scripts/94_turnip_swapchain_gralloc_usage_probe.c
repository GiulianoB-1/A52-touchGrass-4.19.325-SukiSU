#define VK_USE_PLATFORM_ANDROID_KHR 1
#include <vulkan/vulkan.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* VK_ANDROID_native_buffer is a private Android platform extension and is
 * intentionally absent from the public NDK Vulkan headers.  Define only the
 * ABI surface needed by this diagnostic, matching Android/Mesa's extension.
 */
#ifndef VK_ANDROID_NATIVE_BUFFER_EXTENSION_NAME
#define VK_ANDROID_NATIVE_BUFFER_EXTENSION_NAME "VK_ANDROID_native_buffer"
#endif

typedef VkFlags VkSwapchainImageUsageFlagsANDROID;
#ifndef VK_SWAPCHAIN_IMAGE_USAGE_SHARED_BIT_ANDROID
#define VK_SWAPCHAIN_IMAGE_USAGE_SHARED_BIT_ANDROID 0x00000001u
#endif

typedef VkResult (VKAPI_PTR *PFN_vkGetSwapchainGrallocUsageANDROID)(
    VkDevice device,
    VkFormat format,
    VkImageUsageFlags imageUsage,
    int *grallocUsage);

typedef VkResult (VKAPI_PTR *PFN_vkGetSwapchainGrallocUsage2ANDROID)(
    VkDevice device,
    VkFormat format,
    VkImageUsageFlags imageUsage,
    VkSwapchainImageUsageFlagsANDROID swapchainImageUsage,
    uint64_t *grallocConsumerUsage,
    uint64_t *grallocProducerUsage);

#ifndef GRALLOC_USAGE_PRIVATE_ALLOC_UBWC
#define GRALLOC_USAGE_PRIVATE_ALLOC_UBWC (UINT64_C(1) << 28)
#endif

static const char *vk_result_name(VkResult r)
{
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
    default: return "VK_OTHER";
    }
}

static int has_device_extension(VkPhysicalDevice physical, const char *name)
{
    uint32_t count = 0;
    if (vkEnumerateDeviceExtensionProperties(physical, NULL, &count, NULL) != VK_SUCCESS)
        return 0;
    VkExtensionProperties *p = calloc(count, sizeof(*p));
    if (!p)
        return 0;
    VkResult r = vkEnumerateDeviceExtensionProperties(physical, NULL, &count, p);
    int found = 0;
    if (r == VK_SUCCESS) {
        for (uint32_t i = 0; i < count; ++i) {
            if (!strcmp(p[i].extensionName, name)) {
                found = 1;
                break;
            }
        }
    }
    free(p);
    return found;
}

static void dump_usage2(PFN_vkGetSwapchainGrallocUsage2ANDROID fn,
                        VkDevice device,
                        const char *label,
                        VkFormat format,
                        VkImageUsageFlags usage,
                        VkSwapchainImageUsageFlagsANDROID swap_usage)
{
    if (!fn) {
        printf("%s.usage2=UNAVAILABLE\n", label);
        return;
    }

    uint64_t consumer = 0, producer = 0;
    VkResult r = fn(device, format, usage, swap_usage, &consumer, &producer);
    printf("%s.usage2.ret=%d:%s\n", label, r, vk_result_name(r));
    if (r == VK_SUCCESS) {
        printf("%s.usage2.consumer=0x%016" PRIx64 "\n", label, consumer);
        printf("%s.usage2.producer=0x%016" PRIx64 "\n", label, producer);
        printf("%s.usage2.combined=0x%016" PRIx64 "\n", label, consumer | producer);
        printf("%s.usage2.ubwc_private_consumer=%s\n", label,
               (consumer & GRALLOC_USAGE_PRIVATE_ALLOC_UBWC) ? "YES" : "NO");
        printf("%s.usage2.ubwc_private_producer=%s\n", label,
               (producer & GRALLOC_USAGE_PRIVATE_ALLOC_UBWC) ? "YES" : "NO");
        printf("%s.usage2.ubwc_private_any=%s\n", label,
               ((consumer | producer) & GRALLOC_USAGE_PRIVATE_ALLOC_UBWC) ? "YES" : "NO");
    }
}

static void dump_usage1(PFN_vkGetSwapchainGrallocUsageANDROID fn,
                        VkDevice device,
                        const char *label,
                        VkFormat format,
                        VkImageUsageFlags usage)
{
    if (!fn) {
        printf("%s.usage1=UNAVAILABLE\n", label);
        return;
    }

    int gralloc = 0;
    VkResult r = fn(device, format, usage, &gralloc);
    uint64_t u = (uint32_t)gralloc;
    printf("%s.usage1.ret=%d:%s\n", label, r, vk_result_name(r));
    if (r == VK_SUCCESS) {
        printf("%s.usage1.gralloc=0x%08x\n", label, (uint32_t)gralloc);
        printf("%s.usage1.ubwc_private=%s\n", label,
               (u & GRALLOC_USAGE_PRIVATE_ALLOC_UBWC) ? "YES" : "NO");
    }
}

int main(void)
{
    printf("========== touchGrass Vulkan Swapchain Gralloc Usage DIAGNOSTIC v0.34 ==========\n");
    printf("qcom_private_ubwc_bit=0x%016" PRIx64 "\n", GRALLOC_USAGE_PRIVATE_ALLOC_UBWC);

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "touchGrass-swapchain-usage-diag",
        .applicationVersion = 1,
        .pEngineName = "touchGrass",
        .engineVersion = 1,
        .apiVersion = VK_API_VERSION_1_1,
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

    uint32_t count = 0;
    r = vkEnumeratePhysicalDevices(instance, &count, NULL);
    if (r != VK_SUCCESS || count == 0) {
        printf("vkEnumeratePhysicalDevices=%d:%s count=%u\n", r, vk_result_name(r), count);
        vkDestroyInstance(instance, NULL);
        return 11;
    }

    VkPhysicalDevice *phys = calloc(count, sizeof(*phys));
    if (!phys) {
        vkDestroyInstance(instance, NULL);
        return 12;
    }
    r = vkEnumeratePhysicalDevices(instance, &count, phys);
    if (r != VK_SUCCESS) {
        free(phys);
        vkDestroyInstance(instance, NULL);
        return 13;
    }
    VkPhysicalDevice physical = phys[0];
    free(phys);

    VkPhysicalDeviceProperties props;
    vkGetPhysicalDeviceProperties(physical, &props);
    printf("device_name=%s\n", props.deviceName);
    printf("device_api=%u.%u.%u\n",
           VK_VERSION_MAJOR(props.apiVersion),
           VK_VERSION_MINOR(props.apiVersion),
           VK_VERSION_PATCH(props.apiVersion));

    int has_anb = has_device_extension(physical, VK_ANDROID_NATIVE_BUFFER_EXTENSION_NAME);
    printf("VK_ANDROID_native_buffer=%s\n", has_anb ? "YES" : "NO");

    float priority = 1.0f;
    VkDeviceQueueCreateInfo qci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = 0,
        .queueCount = 1,
        .pQueuePriorities = &priority,
    };
    const char *exts[] = { VK_ANDROID_NATIVE_BUFFER_EXTENSION_NAME };
    VkDeviceCreateInfo dci = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1,
        .pQueueCreateInfos = &qci,
        .enabledExtensionCount = has_anb ? 1u : 0u,
        .ppEnabledExtensionNames = has_anb ? exts : NULL,
    };

    VkDevice device = VK_NULL_HANDLE;
    r = vkCreateDevice(physical, &dci, NULL, &device);
    printf("vkCreateDevice=%d:%s\n", r, vk_result_name(r));
    if (r != VK_SUCCESS) {
        vkDestroyInstance(instance, NULL);
        return 14;
    }

    PFN_vkGetSwapchainGrallocUsage2ANDROID usage2 =
        (PFN_vkGetSwapchainGrallocUsage2ANDROID)
        vkGetDeviceProcAddr(device, "vkGetSwapchainGrallocUsage2ANDROID");
    PFN_vkGetSwapchainGrallocUsageANDROID usage1 =
        (PFN_vkGetSwapchainGrallocUsageANDROID)
        vkGetDeviceProcAddr(device, "vkGetSwapchainGrallocUsageANDROID");

    printf("proc.vkGetSwapchainGrallocUsage2ANDROID=%s\n", usage2 ? "AVAILABLE" : "NULL");
    printf("proc.vkGetSwapchainGrallocUsageANDROID=%s\n", usage1 ? "AVAILABLE" : "NULL");

    struct test_case {
        const char *name;
        VkFormat format;
        VkImageUsageFlags usage;
        VkSwapchainImageUsageFlagsANDROID swap_usage;
    } cases[] = {
        {"rgba8_color", VK_FORMAT_R8G8B8A8_UNORM, VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, 0},
        {"rgba8_color_sampled", VK_FORMAT_R8G8B8A8_UNORM,
         VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT, 0},
        {"rgba8_color_xfer", VK_FORMAT_R8G8B8A8_UNORM,
         VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT |
         VK_IMAGE_USAGE_TRANSFER_DST_BIT, 0},
        {"bgra8_color", VK_FORMAT_B8G8R8A8_UNORM, VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, 0},
        {"rgba8_shared", VK_FORMAT_R8G8B8A8_UNORM,
         VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
         VK_SWAPCHAIN_IMAGE_USAGE_SHARED_BIT_ANDROID},
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        printf("\n========== %s ==========\n", cases[i].name);
        printf("%s.format=%d\n", cases[i].name, cases[i].format);
        printf("%s.imageUsage=0x%08x\n", cases[i].name, cases[i].usage);
        printf("%s.swapchainImageUsage=0x%08x\n", cases[i].name, cases[i].swap_usage);
        dump_usage2(usage2, device, cases[i].name, cases[i].format,
                    cases[i].usage, cases[i].swap_usage);
        dump_usage1(usage1, device, cases[i].name, cases[i].format,
                    cases[i].usage);
    }

    vkDestroyDevice(device, NULL);
    vkDestroyInstance(instance, NULL);
    printf("\nSWAPCHAIN_GRALLOC_USAGE_DIAG_STATUS=COMPLETE\n");
    return 0;
}
