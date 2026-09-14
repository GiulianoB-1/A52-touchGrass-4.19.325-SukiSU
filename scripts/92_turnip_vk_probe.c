#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>

static void print_version(const char *key, uint32_t v)
{
    printf("%s=%u.%u.%u\n", key,
           VK_API_VERSION_MAJOR(v),
           VK_API_VERSION_MINOR(v),
           VK_API_VERSION_PATCH(v));
}

int main(void)
{
    uint32_t loader_version = VK_API_VERSION_1_0;
    PFN_vkEnumerateInstanceVersion enumerate_instance_version =
        (PFN_vkEnumerateInstanceVersion)vkGetInstanceProcAddr(NULL, "vkEnumerateInstanceVersion");

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
        .pApplicationName = "touchGrass Turnip A619 probe",
        .applicationVersion = VK_MAKE_VERSION(1, 0, 0),
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
        VkResult er = vkEnumerateDeviceExtensionProperties(devices[i], NULL, &ext_count, NULL);
        printf("device[%u].extension_query_result=%d\n", i, er);
        printf("device[%u].extension_count=%u\n", i, ext_count);

        if (er == VK_SUCCESS && ext_count > 0) {
            VkExtensionProperties *exts = calloc(ext_count, sizeof(*exts));
            if (exts) {
                er = vkEnumerateDeviceExtensionProperties(devices[i], NULL, &ext_count, exts);
                int has_swapchain = 0;
                int has_timeline = 0;
                int has_dynamic_rendering = 0;
                for (uint32_t j = 0; er == VK_SUCCESS && j < ext_count; ++j) {
                    if (!strcmp(exts[j].extensionName, VK_KHR_SWAPCHAIN_EXTENSION_NAME))
                        has_swapchain = 1;
                    if (!strcmp(exts[j].extensionName, "VK_KHR_timeline_semaphore"))
                        has_timeline = 1;
                    if (!strcmp(exts[j].extensionName, "VK_KHR_dynamic_rendering"))
                        has_dynamic_rendering = 1;
                }
                printf("device[%u].has_VK_KHR_swapchain=%d\n", i, has_swapchain);
                printf("device[%u].has_VK_KHR_timeline_semaphore=%d\n", i, has_timeline);
                printf("device[%u].has_VK_KHR_dynamic_rendering=%d\n", i, has_dynamic_rendering);
                free(exts);
            }
        }
    }

    free(devices);
    vkDestroyInstance(instance, NULL);
    printf("probe_status=PASS\n");
    return 0;
}
