/* Test-only declarations. This harness never links libusb or opens a device. */
#ifndef FPSHD_TEST_LIBUSB_H
#define FPSHD_TEST_LIBUSB_H
#include <stdint.h>
typedef struct libusb_context libusb_context;
typedef struct libusb_device_handle libusb_device_handle;
typedef struct libusb_device libusb_device;
#define LIBUSB_TRANSFER_TYPE_MASK 3
#define LIBUSB_TRANSFER_TYPE_BULK 2
#define LIBUSB_ERROR_IO -1
#define LIBUSB_ERROR_NO_DEVICE -4
#define LIBUSB_ERROR_TIMEOUT -7
#define LIBUSB_ERROR_OVERFLOW -8
#define LIBUSB_ERROR_PIPE -9
struct libusb_endpoint_descriptor {
    uint8_t bEndpointAddress, bmAttributes;
    uint16_t wMaxPacketSize;
};
struct libusb_interface_descriptor {
    uint8_t bInterfaceNumber, bAlternateSetting, bNumEndpoints;
    const struct libusb_endpoint_descriptor *endpoint;
};
struct libusb_interface {
    const struct libusb_interface_descriptor *altsetting;
    int num_altsetting;
};
struct libusb_config_descriptor {
    uint8_t bNumInterfaces;
    const struct libusb_interface *interface;
};
int libusb_init(libusb_context **);
void libusb_exit(libusb_context *);
libusb_device_handle *libusb_open_device_with_vid_pid(libusb_context *, uint16_t, uint16_t);
int libusb_set_auto_detach_kernel_driver(libusb_device_handle *, int);
int libusb_claim_interface(libusb_device_handle *, int);
int libusb_release_interface(libusb_device_handle *, int);
void libusb_close(libusb_device_handle *);
libusb_device *libusb_get_device(libusb_device_handle *);
int libusb_get_active_config_descriptor(libusb_device *, struct libusb_config_descriptor **);
void libusb_free_config_descriptor(struct libusb_config_descriptor *);
int libusb_bulk_transfer(libusb_device_handle *, unsigned char, unsigned char *, int, int *, unsigned);
int libusb_clear_halt(libusb_device_handle *, unsigned char);
const char *libusb_error_name(int);
#endif
