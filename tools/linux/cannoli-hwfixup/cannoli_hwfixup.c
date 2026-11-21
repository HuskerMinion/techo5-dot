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
 * nodes= names the parts, by path; the default is the DVT/PVT aluminium board's (the first
 * unit: ISSI IS31FL3246 at 0-0031, Maxim MAX98396 at 3-0038, TI TLV320ADC5140 at 4-004c).
 * A node already populated, or whose address is taken, is left alone.
 */
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>

#define MAX_NODES 8

static char *nodes[MAX_NODES] = {
	"/soc/i2c@10019100/is31fl3246@31",
	"/soc/i2c@11007000/max98396@38",
	"/soc/i2c@11008000/tlv320adc5140@4c",
};
static int nnodes = 3;
module_param_array(nodes, charp, &nnodes, 0444);
MODULE_PARM_DESC(nodes, "device tree paths of the I2C parts to bring up");

static struct i2c_client *made[MAX_NODES];

static struct i2c_client *bring_up(const char *path)
{
	struct device_node *np, *bus;
	struct i2c_adapter *adap;
	struct i2c_board_info info;
	struct i2c_client *client;
	int ret;

	np = of_find_node_by_path(path);
	if (!np) {
		pr_warn("cannoli_hwfixup: %s: no such node\n", path);
		return NULL;
	}
	if (of_node_check_flag(np, OF_POPULATED)) {
		pr_info("cannoli_hwfixup: %s: already populated\n", path);
		of_node_put(np);
		return NULL;
	}
	bus = of_get_parent(np);
	adap = bus ? of_find_i2c_adapter_by_node(bus) : NULL;
	of_node_put(bus);
	if (!adap) {
		pr_warn("cannoli_hwfixup: %s: its bus has no adapter\n", path);
		of_node_put(np);
		return NULL;
	}

	ret = of_i2c_get_board_info(&adap->dev, np, &info);
	if (ret) {
		pr_warn("cannoli_hwfixup: %s: no board info (%d)\n", path, ret);
		goto out;
	}
	client = i2c_new_client_device(adap, &info);
	if (IS_ERR(client)) {
		pr_warn("cannoli_hwfixup: %s: device not made (%ld)\n", path, PTR_ERR(client));
		client = NULL;
		goto out;
	}
	of_node_set_flag(np, OF_POPULATED);
	pr_info("cannoli_hwfixup: %s -> %s (%s)\n", path, dev_name(&client->dev), info.type);
	i2c_put_adapter(adap);
	return client;	/* the client holds np now */
out:
	i2c_put_adapter(adap);
	of_node_put(np);
	return NULL;
}

static int __init cannoli_hwfixup_init(void)
{
	int i, n = 0;

	for (i = 0; i < nnodes && i < MAX_NODES; i++) {
		made[i] = bring_up(nodes[i]);
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
