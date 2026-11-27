/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ring_id.h: whether the chip on the Echo Dot 5th gen's ring address reads as a TI LP5036 (#780), from
 * reads alone. Plain C, so tools/test_install_cannoli.py builds it on the host too.
 *
 * An LP5036's reserved bits read 0 (TI SNVS638): DEVICE_CONFIG0 (0x00) all but Chip_EN (bit 6),
 * DEVICE_CONFIG1 (0x01) bits 7:6, LED_CONFIG1 (0x03) bits 7:4. DEVICE_CONFIG1's power-on 0x3c
 * (Log_Scale_EN, Power_Save_EN, Auto_Incr_EN, PWM_Dithering_EN) is kept by the lp50xx driver, which
 * sets only bits 1:0 (Max_Current_Option, LED_Global_Off): a cold boot reads 0x3c, a unit Fire OS has
 * driven 0x3c to 0x3f (one did read 0x40 0x3f). An IS31FL3246 at the address has a PWM byte at 0x01,
 * 0 from power-on, and if it takes no reads at all the read fails before this is asked.
 */
#ifndef RING_ID_H
#define RING_ID_H

#define LP5036_DEV_CFG0	0x00
#define LP5036_DEV_CFG1	0x01
#define LP5036_LED_CFG1	0x03

static inline int ring_is_lp5036(int cfg0, int cfg1, int led_cfg1)
{
	if (cfg0 < 0 || cfg1 < 0 || led_cfg1 < 0)
		return 0;	/* a read failed */
	return (cfg0 & ~0x40) == 0 && (cfg1 & 0xfc) == 0x3c && (led_cfg1 & 0xf0) == 0;
}

#endif
