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
 * asks the chip (find_ring), a path brings up that node, "none" none. A ring named in nodes= stands.
 */
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>

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
MODULE_PARM_DESC(ring, "the ring's node: auto (ask the chip), a device tree path, or none");

#define RING_BUS "/soc/i2c@10019100"

/* The LP5036's registers (TI SNVS638, and leds-lp50xx.c). */
#define LP5036_DEV_CFG0		0x00
#define LP5036_DEV_CFG1		0x01
#define LP5036_LED_CFG1		0x03
#define LP5036_BNK_BRT		0x04
#define LP5036_RESET		0x38
#define LP50XX_SW_RESET		0xff

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

static int ring_write(struct i2c_adapter *adap, u16 addr, u8 reg, u8 val)
{
	union i2c_smbus_data d = { .byte = val };

	return i2c_smbus_xfer(adap, addr, 0, I2C_SMBUS_WRITE, reg, I2C_SMBUS_BYTE_DATA, &d);
}

/*
 * is_lp5036: whether the chip at addr is an LP5036. Its reset register puts every register at its
 * datasheet default, which an LP5036 then reads back: DEVICE_CONFIG0 0x00, DEVICE_CONFIG1 0x3c,
 * LED_CONFIG1's reserved high nibble 0, BANK_BRIGHTNESS 0xff. On an IS31FL3246 the reset write lands
 * on one PWM byte of a chip still in software shutdown, which its driver's probe then resets anyway.
 */
static bool is_lp5036(struct i2c_adapter *adap, u16 addr)
{
	if (ring_write(adap, addr, LP5036_RESET, LP50XX_SW_RESET) < 0)
		return false;
	return ring_read(adap, addr, LP5036_DEV_CFG0) == 0x00 &&
	       ring_read(adap, addr, LP5036_DEV_CFG1) == 0x3c &&
	       (ring_read(adap, addr, LP5036_LED_CFG1) & ~0x0f) == 0 &&
	       ring_read(adap, addr, LP5036_BNK_BRT) == 0xff;
}

/*
 * find_ring: the ring's node, by the chip that answers on the ring's bus: an LP5036 or an IS31FL3246 at
 * 0x30-0x33 (both default to 0x31, tried first), else an AW21036 at 0x34-0x37. Nothing answering gives
 * the first unit's IS31FL3246 at 0x31, as before.
 */
static const char *find_ring(char *buf, size_t len)
{
	static const u16 order[] = { 0x31, 0x30, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37 };
	struct device_node *bus;
	struct i2c_adapter *adap;
	const char *chip = NULL;
	u16 addr = 0x31;
	int i;

	bus = of_find_node_by_path(RING_BUS);
	adap = bus ? of_find_i2c_adapter_by_node(bus) : NULL;
	of_node_put(bus);
	if (!adap) {
		pr_warn("cannoli_hwfixup: " RING_BUS " has no adapter\n");
		goto out;
	}
	for (i = 0; i < ARRAY_SIZE(order) && !chip; i++) {
		if (ring_read(adap, order[i], LP5036_DEV_CFG0) < 0)
			continue;	/* nothing acknowledged it */
		addr = order[i];
		if (addr >= 0x34)
			chip = "aw21036";
		else if (is_lp5036(adap, addr))
			chip = "lp5036";
		else
			chip = "is31fl3246";
	}
	i2c_put_adapter(adap);
out:
	if (!chip)
		pr_info("cannoli_hwfixup: nothing answers on the ring's bus, taking is31fl3246@31\n");
	snprintf(buf, len, RING_BUS "/%s@%x", chip ? chip : "is31fl3246", addr);
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
