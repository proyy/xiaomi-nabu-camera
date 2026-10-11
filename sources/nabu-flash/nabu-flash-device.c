// SPDX-License-Identifier: GPL-2.0-only
// Copyright (c) 2026 zouhong <admin@proyy.com> (nabu adaptations)
/* Nabu OEM wiring: PM8150L SID5, register d300, LED channel 0 (binding index 1).
 * Software nodes add the missing flash description to the original 6.1 DT.
 * Limits intentionally below OEM 500 mA torch / 1500 mA flash.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/regmap.h>

static const struct property_entry controller_properties[] = {
	PROPERTY_ENTRY_U32("reg", 0xd300), {}
};
static const struct property_entry led_properties[] = {
	PROPERTY_ENTRY_STRING("label", "nabu:white:flash"),
	PROPERTY_ENTRY_U32("led-sources", 1),
	PROPERTY_ENTRY_U32("led-max-microamp", 100000),
	PROPERTY_ENTRY_U32("flash-max-microamp", 250000),
	PROPERTY_ENTRY_U32("flash-max-timeout-us", 300000), {}
};
static const struct software_node controller = {
	.name = "nabu-flash", .properties = controller_properties,
};
static const struct software_node led = {
	.name = "led-0", .parent = &controller, .properties = led_properties,
};
static const struct software_node *nodes[] = { &controller, &led, NULL };
static struct platform_device *flash;
static struct device *pmic;

static int __init nabu_flash_init(void)
{
	struct device *arb, *bus;
	struct regmap *map;
	unsigned int type, subtype;
	int rc;
	if (!of_machine_is_compatible("xiaomi,nabu"))
		return -ENODEV;
	arb = bus_find_device_by_name(&platform_bus_type, NULL, "c440000.spmi");
	if (!arb)
		return -ENODEV;
	bus = device_find_child_by_name(arb, "spmi-0");
	put_device(arb);
	if (!bus)
		return -ENODEV;
	pmic = device_find_child_by_name(bus, "0-05");
	put_device(bus);
	if (!pmic)
		return -ENODEV;
	map = dev_get_regmap(pmic, NULL);
	if (!of_device_is_compatible(pmic->of_node, "qcom,pm8150l") || !map) {
		rc = -ENODEV;
		goto put;
	}
	rc = regmap_read(map, 0xd304, &type);
	if (rc)
		goto put;
	rc = regmap_read(map, 0xd305, &subtype);
	if (rc)
		goto put;
	if (type != 0x18 || subtype != 0x04) {
		rc = -ENODEV;
		goto put;
	}
	rc = software_node_register_node_group(nodes);
	if (rc)
		goto put;
	flash = platform_device_alloc("leds-qcom-flash", PLATFORM_DEVID_NONE);
	if (!flash) {
		rc = -ENOMEM;
		goto nodes;
	}
	flash->dev.parent = pmic;
	flash->dev.fwnode = software_node_fwnode(&controller);
	rc = platform_device_add(flash);
	if (!rc)
		return 0;
	platform_device_put(flash);
nodes:
	software_node_unregister_node_group(nodes);
put:
	put_device(pmic);
	return rc;
}
static void __exit nabu_flash_exit(void)
{
	platform_device_unregister(flash);
	software_node_unregister_node_group(nodes);
	put_device(pmic);
}
module_init(nabu_flash_init);
module_exit(nabu_flash_exit);
MODULE_SOFTDEP("pre: nabu-qcom-flash");
MODULE_DESCRIPTION("Xiaomi nabu PM8150L flash device description");
MODULE_LICENSE("GPL");
