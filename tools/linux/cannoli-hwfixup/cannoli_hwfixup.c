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
 * nodes= names the parts, by path; the default is the DVT/PVT aluminum board's amplifier and converter
 * (Maxim MAX98396 at 3-0038, TI TLV320ADC5140 at 4-004c). A node already populated, or whose address
 * is taken, is left alone.
 *
 * The ring's chip differs between boards at the same address: an ISSI IS31FL3246 on the first unit, a
 * TI LP5036 on others (#780), and the tree has an AW21036 too. Either driver binds to either chip,
 * neither reads an ID, and the wrong one leaves the ring dark, so ring= picks it: "auto" (the default)
 * reads the chip (find_ring), a path brings up that node, "none" none. A ring named in nodes= stands.
 * Nothing is written to a chip before its driver has it.
 */
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>

#include "ring_id.h"

#define MAX_NODES 8

static char *nodes[MAX_NODES] = {
	"/soc/i2c@11007000/max98396@38",
	"/soc/i2c@11008000/tlv320adc5140@4c",
};
static int nnodes = 2;
module_param_array(nodes, charp, &nnodes, 0444);
MODULE_PARM_DESC(nodes, "device tree paths of the I2C parts to bring up");

static char *ring = "auto";
module_param(ring, charp, 0444);
MODULE_PARM_DESC(ring, "the ring's node: auto (read the chip), a device tree path, or none");

#define RING_BUS "/soc/i2c@10019100"

static struct i2c_client *made[MAX_NODES + 1];

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

static int ring_read(struct i2c_adapter *adap, u16 addr, u8 reg)
{
	union i2c_smbus_data d;
	int ret = i2c_smbus_xfer(adap, addr, 0, I2C_SMBUS_READ, reg, I2C_SMBUS_BYTE_DATA, &d);

	return ret < 0 ? ret : d.byte;
}

/*
 * find_ring: the ring's node. An LP5036 at 0x30-0x33 (0x31, its address on the boards seen, first) is
 * taken by its registers (ring_is_lp5036); anything else is the first unit's IS31FL3246 at 0x31, as
 * before. An AW21036 is ring= only: no board with one has been seen.
 */
static const char *find_ring(char *buf, size_t len)
{
	static const u16 order[] = { 0x31, 0x30, 0x32, 0x33 };
	struct device_node *bus;
	struct i2c_adapter *adap;
	const char *chip = "is31fl3246";
	u16 addr = 0x31;
	int i;

	bus = of_find_node_by_path(RING_BUS);
	adap = bus ? of_find_i2c_adapter_by_node(bus) : NULL;
	of_node_put(bus);
	if (!adap) {
		pr_warn("cannoli_hwfixup: " RING_BUS " has no adapter\n");
		goto out;
	}
	for (i = 0; i < ARRAY_SIZE(order); i++) {
		int c0 = ring_read(adap, order[i], LP5036_DEV_CFG0);
		int c1 = c0 < 0 ? c0 : ring_read(adap, order[i], LP5036_DEV_CFG1);
		int l1 = c1 < 0 ? c1 : ring_read(adap, order[i], LP5036_LED_CFG1);

		if (c0 >= 0)
			pr_info("cannoli_hwfixup: ring 0-%04x reads %02x %02x %02x\n", order[i], c0 & 0xff,
				c1 & 0xff, l1 & 0xff);
		if (ring_is_lp5036(c0, c1, l1)) {
			chip = "lp5036";
			addr = order[i];
			break;
		}
	}
	i2c_put_adapter(adap);
out:
	snprintf(buf, len, RING_BUS "/%s@%x", chip, addr);
	return buf;
}

static int __init cannoli_hwfixup_init(void)
{
	char buf[64];
	const char *rp = NULL;
	int i, n = 0, want = 0;
	bool have_ring = false;

	for (i = 0; i < nnodes && i < MAX_NODES; i++) {
		if (!strncmp(nodes[i], RING_BUS "/", strlen(RING_BUS "/")))
			have_ring = true;
		made[i] = bring_up(nodes[i]);
		want++;
		if (made[i])
			n++;
	}
	if (!have_ring && strcmp(ring, "none"))
		rp = strcmp(ring, "auto") ? ring : find_ring(buf, sizeof(buf));
	if (rp) {
		made[MAX_NODES] = bring_up(rp);
		want++;
		if (made[MAX_NODES])
			n++;
	}
	pr_info("cannoli_hwfixup: %d of %d parts brought up\n", n, want);
	return 0;
}

static void __exit cannoli_hwfixup_exit(void)
{
	int i;

	for (i = MAX_NODES; i >= 0; i--) {
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
