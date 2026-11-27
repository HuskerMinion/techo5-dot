// SPDX-License-Identifier: GPL-2.0
/*
 * cannoli_hwfixup: the Echo Dot 5th gen's ring, speaker amplifier and microphone converter on a slot-B
 * (recovery) boot.
 *
 * Every device tree in cannoli's FIT ships the board's I2C parts with status "disabled", beside the
 * alternatives other board revisions carry, and Amazon's U-Boot enables the ones this board has as it
 * boots. On a recovery boot it skips that ("recovery boot mode detected, skipping LEDS"), and slot B is
 * booted as cannoli-pvt-aluminum-recovery, so Linux there has no ring, no amplifier and no ADC, and the
 * sound card defers for good on ETDM1_IN. The tree is signed and this kernel has no overlays.
 *
 * So the devices are made here, the way the I2C core makes them from the tree (of_i2c_get_board_info,
 * i2c_new_client_device), each with its own node: the drivers read their properties from it (the ring's
 * twelve modules, the codecs' settings), and the sound card, which names the codecs by phandle, finds
 * them once they bind. The node's status is not looked at again after the bus has been populated.
 *
 * Boards differ in their microphone converter: a TI TLV320ADC5140 at 4-004c or an AKM AK5704 at
 * 4-0010. An entry may list alternatives separated by '|'; the first that answers a one-byte read
 * (with the converters' reset-adc supply on) is made. If that is not the first alternative, the sound
 * card's links naming the first by phandle are pointed at it, with its DAI name, before it is made:
 * the deferred card reads them on its next probe. Without OF_DYNAMIC the properties are rewritten in
 * place.
 *
 * nodes= names the parts, by path; the default is the DVT/PVT aluminum board's (ISSI IS31FL3246 at
 * 0-0031, Maxim MAX98396 at 3-0038, either converter). A node already populated is left alone.
 */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/string.h>

#define MAX_NODES 8
#define MAX_ALTS 4

static char *nodes[MAX_NODES] = {
	"/soc/i2c@10019100/is31fl3246@31",
	"/soc/i2c@11007000/max98396@38",
	"/soc/i2c@11008000/tlv320adc5140@4c|/soc/i2c@11008000/ak5704@10",
};
static int nnodes = 3;
module_param_array(nodes, charp, &nnodes, 0444);
MODULE_PARM_DESC(nodes, "device tree paths of the I2C parts to bring up; '|' between alternatives");

static struct i2c_client *made[MAX_NODES];

/* The DAI (and codec name) each converter's driver registers. */
static const struct {
	const char *compatible;
	const char *dai;
	const char *name;
} codec_dais[] = {
	{ "ti,tlv320adc5140", "tlv320adc5140-codec-a", "tlv320adc5140_1" },
	{ "akm,ak5704", "ak5704-aif-a", "ak5704-codec-a" },
};

/* bus_of is the adapter of the bus a node sits on, or NULL. */
static struct i2c_adapter *bus_of(struct device_node *np)
{
	struct device_node *bus = of_get_parent(np);
	struct i2c_adapter *adap = bus ? of_find_i2c_adapter_by_node(bus) : NULL;

	of_node_put(bus);
	return adap;
}

/*
 * supply_on enables the node's reset-adc supply for the probe, through a placeholder device that only
 * carries the node for the lookup. NULL: no supply; the probe goes ahead without it.
 */
static struct regulator *supply_on(struct device_node *np, struct platform_device **holder)
{
	struct platform_device *pdev;
	struct regulator *reg;

	*holder = NULL;
	if (!of_find_property(np, "reset-adc-regulator-supply", NULL))
		return NULL;
	pdev = platform_device_alloc("cannoli-hwfixup-probe", PLATFORM_DEVID_AUTO);
	if (!pdev)
		return NULL;
	pdev->dev.of_node = of_node_get(np);	/* put when the device is released */
	pdev->driver_override = kstrdup("none", GFP_KERNEL);	/* no driver binds to it */
	if (!pdev->driver_override) {
		platform_device_put(pdev);
		return NULL;
	}
	if (platform_device_add(pdev)) {
		platform_device_put(pdev);
		return NULL;
	}
	reg = regulator_get_optional(&pdev->dev, "reset-adc-regulator");
	if (IS_ERR(reg) || regulator_enable(reg)) {
		if (!IS_ERR(reg))
			regulator_put(reg);
		platform_device_unregister(pdev);
		return NULL;
	}
	/* Some time out of reset on top of the supply's startup delay. */
	usleep_range(5000, 6000);
	*holder = pdev;
	return reg;
}

static void supply_off(struct regulator *reg, struct platform_device *holder)
{
	if (reg) {
		regulator_disable(reg);
		regulator_put(reg);
	}
	if (holder)
		platform_device_unregister(holder);
}

/* answers is whether a part acknowledges a one-byte read at its address. */
static bool answers(struct device_node *np)
{
	struct i2c_adapter *adap = bus_of(np);
	union i2c_smbus_data data;
	u32 addr;
	bool ok = false;

	if (adap && !of_property_read_u32(np, "reg", &addr))
		ok = i2c_smbus_xfer(adap, addr, 0, I2C_SMBUS_READ, 0, I2C_SMBUS_BYTE_DATA, &data) >= 0;
	if (adap)
		i2c_put_adapter(adap);
	return ok;
}

/* set_string gives a property a new string value; the old one stays where it was. */
static void set_string(struct device_node *np, const char *name, const char *value)
{
	struct property *prop = of_find_property(np, name, NULL);
	char *v;

	if (!prop)
		return;
	v = kstrdup(value, GFP_KERNEL);
	if (!v)
		return;
	prop->value = v;
	prop->length = strlen(v) + 1;
}

/*
 * relink points the card's <link>-audio-codec properties that name was at now, with now's DAI, under
 * the card's device lock so a probe never reads them half written.
 */
static void relink(struct device_node *was, struct device_node *now)
{
	struct device_node *card;
	struct platform_device *pdev;
	const char *dai = NULL, *cname = NULL;
	struct property *prop;
	size_t i;

	for (i = 0; i < ARRAY_SIZE(codec_dais); i++) {
		if (of_device_is_compatible(now, codec_dais[i].compatible)) {
			dai = codec_dais[i].dai;
			cname = codec_dais[i].name;
		}
	}
	if (!dai || !now->phandle) {
		pr_warn("cannoli_hwfixup: %pOF: no DAI or phandle known; the card is left as it is\n", now);
		return;
	}
	card = of_find_node_by_path("/sound");
	if (!card)
		return;
	pdev = of_find_device_by_node(card);
	if (pdev)
		device_lock(&pdev->dev);
	for_each_property_of_node(card, prop) {
		size_t len = strlen(prop->name);
		const char *suffix = "-audio-codec";
		char link[96];

		if (len <= strlen(suffix) || strcmp(prop->name + len - strlen(suffix), suffix) ||
		    prop->length != sizeof(__be32) || be32_to_cpup(prop->value) != was->phandle)
			continue;
		if (len - strlen(suffix) >= sizeof(link) - 20)
			continue;
		memcpy(link, prop->name, len - strlen(suffix));
		link[len - strlen(suffix)] = '\0';
		{
			char name[128];

			snprintf(name, sizeof(name), "%s-codec-dai-name", link);
			set_string(card, name, dai);
			snprintf(name, sizeof(name), "%s-audio-codec-name", link);
			set_string(card, name, cname);
		}
		*(__be32 *)prop->value = cpu_to_be32(now->phandle);
		pr_info("cannoli_hwfixup: %s now names %pOF (%s)\n", prop->name, now, dai);
	}
	if (pdev) {
		device_unlock(&pdev->dev);
		put_device(&pdev->dev);
	}
	of_node_put(card);
}

/*
 * choose is the node to bring up for an entry: its path, or the first alternative that answers.
 * *first is the first alternative, held, when another was chosen.
 */
static struct device_node *choose(char *entry, struct device_node **first)
{
	struct device_node *alts[MAX_ALTS], *pick = NULL;
	struct platform_device *holder;
	struct regulator *reg;
	char *s = entry, *path;
	int n = 0, i;

	*first = NULL;
	if (!strchr(entry, '|'))
		return of_find_node_by_path(entry);
	entry = kstrdup(entry, GFP_KERNEL);
	if (!entry)
		return NULL;
	s = entry;
	while ((path = strsep(&s, "|")) && n < MAX_ALTS) {
		alts[n] = of_find_node_by_path(path);
		if (!alts[n])
			pr_warn("cannoli_hwfixup: %s: no such node\n", path);
		else
			n++;
	}
	kfree(entry);
	if (n == 0)
		return NULL;
	reg = supply_on(alts[0], &holder);
	for (i = 0; i < n && !pick; i++) {
		if (answers(alts[i]))
			pick = alts[i];
	}
	supply_off(reg, holder);
	if (pick) {
		pr_info("cannoli_hwfixup: %pOF answers\n", pick);
	} else {
		pr_warn("cannoli_hwfixup: none of %pOF's alternatives answers; bringing it up\n", alts[0]);
		pick = alts[0];
	}
	for (i = 0; i < n; i++) {
		if (alts[i] == pick)
			continue;
		if (i == 0 && pick != alts[0])
			*first = alts[0];
		else
			of_node_put(alts[i]);
	}
	return pick;
}

/* bring_up makes np's device; it takes np's reference. */
static struct i2c_client *bring_up(struct device_node *np)
{
	struct i2c_adapter *adap;
	struct i2c_board_info info;
	struct i2c_client *client;
	int ret;

	if (of_node_check_flag(np, OF_POPULATED)) {
		pr_info("cannoli_hwfixup: %pOF: already populated\n", np);
		of_node_put(np);
		return NULL;
	}
	adap = bus_of(np);
	if (!adap) {
		pr_warn("cannoli_hwfixup: %pOF: its bus has no adapter\n", np);
		of_node_put(np);
		return NULL;
	}

	ret = of_i2c_get_board_info(&adap->dev, np, &info);
	if (ret) {
		pr_warn("cannoli_hwfixup: %pOF: no board info (%d)\n", np, ret);
		goto out;
	}
	client = i2c_new_client_device(adap, &info);
	if (IS_ERR(client)) {
		pr_warn("cannoli_hwfixup: %pOF: device not made (%ld)\n", np, PTR_ERR(client));
		client = NULL;
		goto out;
	}
	of_node_set_flag(np, OF_POPULATED);
	pr_info("cannoli_hwfixup: %pOF -> %s (%s)\n", np, dev_name(&client->dev), info.type);
	i2c_put_adapter(adap);
	return client;	/* the client holds np now */
out:
	i2c_put_adapter(adap);
	of_node_put(np);
	return NULL;
}

static int __init cannoli_hwfixup_init(void)
{
	struct device_node *np[MAX_NODES], *first[MAX_NODES];
	int i, n = 0, count = min(nnodes, MAX_NODES);

	/* Every choice and relink before any device is made: each one made can start the card's probe. */
	for (i = 0; i < count; i++) {
		np[i] = choose(nodes[i], &first[i]);
		if (!np[i])
			pr_warn("cannoli_hwfixup: %s: no such node\n", nodes[i]);
		else if (first[i])
			relink(first[i], np[i]);
		of_node_put(first[i]);
	}
	for (i = 0; i < count; i++) {
		made[i] = np[i] ? bring_up(np[i]) : NULL;
		if (made[i])
			n++;
	}
	pr_info("cannoli_hwfixup: %d of %d parts brought up\n", n, nnodes);
	return 0;
}

static void __exit cannoli_hwfixup_exit(void)
{
	int i;

	for (i = MAX_NODES - 1; i >= 0; i--) {
		if (!made[i])
			continue;
		of_node_clear_flag(made[i]->dev.of_node, OF_POPULATED);
		i2c_unregister_device(made[i]);
	}
}

module_init(cannoli_hwfixup_init);
module_exit(cannoli_hwfixup_exit);
MODULE_DESCRIPTION("Echo Dot 5th gen: the I2C parts U-Boot leaves disabled on a recovery boot");
MODULE_LICENSE("GPL");
