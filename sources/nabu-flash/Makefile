obj-m += nabu-led-flash.o nabu-v4l2-flash.o nabu-qcom-flash.o nabu-flash-device.o
nabu-led-flash-y := led-class-flash.o
nabu-v4l2-flash-y := v4l2-flash-led-class.o
nabu-qcom-flash-y := leds-qcom-flash.o
ccflags-y += -DCONFIG_LEDS_CLASS_FLASH_MODULE=1 -DCONFIG_V4L2_FLASH_LED_CLASS_MODULE=1
