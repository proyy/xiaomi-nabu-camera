// SPDX-License-Identifier: GPL-2.0-only
// Copyright (c) 2026 zouhong <admin@proyy.com> (nabu adaptations)
/* Xiaomi nabu CN3927 advanced-mode VCM.
 * Register protocol: Rockchip cn3927v; tuning: this board's OEM descriptor.
 */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/consumer.h>
#include <media/v4l2-async.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>

#define CN3927_STEP 16
#define CN3927_DEFAULT_DAC 253

struct cn3927 {
	struct v4l2_subdev sd;
	struct v4l2_ctrl_handler ctrls;
	struct v4l2_ctrl *focus;
	struct regulator_bulk_data supplies[2];
	u16 current_dac;
	u16 target_dac;
};

static struct cn3927 *to_cn3927(struct v4l2_subdev *sd)
{
	return container_of(sd, struct cn3927, sd);
}

static int cn3927_write(struct cn3927 *vcm, u8 reg, u16 val, u8 len)
{
	struct i2c_client *client = v4l2_get_subdevdata(&vcm->sd);
	u8 buf[3] = { reg };
	int ret;

	if (len == 2)
		buf[1] = val >> 8;
	buf[len] = val & 0xff;
	ret = i2c_master_send(client, buf, len + 1);
	return ret == len + 1 ? 0 : ret < 0 ? ret : -EIO;
}

static int cn3927_read(struct cn3927 *vcm, u8 reg, u8 len)
{
	struct i2c_client *client = v4l2_get_subdevdata(&vcm->sd);
	u8 buf[2];
	struct i2c_msg msgs[] = {
		{ .addr = client->addr, .len = 1, .buf = &reg },
		{ .addr = client->addr, .flags = I2C_M_RD, .len = len, .buf = buf },
	};
	int ret = i2c_transfer(client->adapter, msgs, ARRAY_SIZE(msgs));

	if (ret != ARRAY_SIZE(msgs))
		return ret < 0 ? ret : -EIO;
	return len == 2 ? (buf[0] << 8) | buf[1] : buf[0];
}

static int cn3927_busy(struct cn3927 *vcm)
{
	return cn3927_read(vcm, 5, 1);
}

static int cn3927_wait_idle(struct cn3927 *vcm)
{
	int status, ret;

	ret = readx_poll_timeout(cn3927_busy, vcm, status,
				status < 0 || !(status & 1), 1000, 10000);
	if (ret)
		return ret;
	if (status < 0)
		return status;
	return 0;
}

static int cn3927_set_dac(struct cn3927 *vcm, u16 val)
{
	int ret = cn3927_wait_idle(vcm);

	if (ret)
		return ret;
	ret = cn3927_write(vcm, 3, val, 2);
	if (!ret)
		vcm->current_dac = val;
	return ret;
}

static int cn3927_move(struct cn3927 *vcm, u16 target)
{
	int next, ret;

	while (vcm->current_dac != target) {
		next = clamp_t(int, target, (int)vcm->current_dac - CN3927_STEP,
			       (int)vcm->current_dac + CN3927_STEP);
		ret = cn3927_set_dac(vcm, next);
		if (ret)
			return ret;
		usleep_range(1000, 1200);
	}
	/* Confirm the actual position before reporting a successful control. */
	ret = cn3927_wait_idle(vcm);
	if (ret)
		return ret;
	ret = cn3927_read(vcm, 3, 2);
	return ret < 0 ? ret : ret == target ? 0 : -EIO;
}

static int cn3927_resume(struct device *dev)
{
	struct cn3927 *vcm = to_cn3927(i2c_get_clientdata(to_i2c_client(dev)));
	static const u8 init[][2] = {
		{ 0xed, 0xab }, { 2, 1 }, { 2, 0 },
		{ 6, 0x84 }, { 7, 1 }, { 8, 89 },
	};
	int i, ret;

	ret = regulator_bulk_enable(ARRAY_SIZE(vcm->supplies), vcm->supplies);
	if (ret)
		return ret;
	usleep_range(1000, 1200);
	for (i = 0; i < ARRAY_SIZE(init); i++) {
		ret = cn3927_write(vcm, init[i][0], init[i][1], 1);
		if (ret)
			goto power_off;
		usleep_range(1000, 1200);
	}
	for (i = 3; i < ARRAY_SIZE(init); i++) {
		ret = cn3927_read(vcm, init[i][0], 1);
		if (ret < 0)
			goto power_off;
		if (ret != init[i][1]) {
			ret = -EIO;
			goto power_off;
		}
	}
	ret = cn3927_read(vcm, 3, 2);
	if (ret < 0)
		goto power_off;
	vcm->current_dac = ret & 0x3ff;
	ret = cn3927_move(vcm, vcm->target_dac);
	if (!ret)
		return 0;
power_off:
	regulator_bulk_disable(ARRAY_SIZE(vcm->supplies), vcm->supplies);
	return ret;
}

static int cn3927_suspend(struct device *dev)
{
	struct cn3927 *vcm = to_cn3927(i2c_get_clientdata(to_i2c_client(dev)));
	int ret;

	ret = cn3927_move(vcm, 0);
	if (ret)
		return ret;
	ret = cn3927_wait_idle(vcm);
	if (ret)
		return ret;
	ret = cn3927_write(vcm, 2, 1, 1);
	if (ret)
		return ret;
	return regulator_bulk_disable(ARRAY_SIZE(vcm->supplies), vcm->supplies);
}

static int cn3927_set_ctrl(struct v4l2_ctrl *ctrl)
{
	struct cn3927 *vcm = container_of(ctrl->handler, struct cn3927, ctrls);
	int active, ret;

	if (ctrl->id != V4L2_CID_FOCUS_ABSOLUTE)
		return -EINVAL;
	active = pm_runtime_get_if_in_use(vcm->sd.dev);
	if (active < 0)
		return active;
	if (!active) {
		vcm->target_dac = ctrl->val;
		return 0;
	}
	ret = cn3927_move(vcm, ctrl->val);
	if (!ret)
		vcm->target_dac = ctrl->val;
	pm_runtime_put(vcm->sd.dev);
	return ret;
}

static const struct v4l2_ctrl_ops cn3927_ctrl_ops = {
	.s_ctrl = cn3927_set_ctrl,
};

static int cn3927_open(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	return pm_runtime_resume_and_get(sd->dev);
}

static int cn3927_close(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	return pm_runtime_put_sync(sd->dev);
}

static const struct v4l2_subdev_internal_ops cn3927_internal_ops = {
	.open = cn3927_open,
	.close = cn3927_close,
};
static const struct v4l2_subdev_ops cn3927_ops = { };

static int cn3927_probe(struct i2c_client *client)
{
	struct cn3927 *vcm;
	int ret;

	vcm = devm_kzalloc(&client->dev, sizeof(*vcm), GFP_KERNEL);
	if (!vcm)
		return -ENOMEM;
	vcm->supplies[0].supply = "vio";
	vcm->supplies[1].supply = "vaf";
	ret = devm_regulator_bulk_get(&client->dev, ARRAY_SIZE(vcm->supplies),
				      vcm->supplies);
	if (ret)
		return dev_err_probe(&client->dev, ret, "VCM supplies unavailable\n");
	v4l2_i2c_subdev_init(&vcm->sd, client, &cn3927_ops);
	vcm->sd.flags |= V4L2_SUBDEV_FL_HAS_DEVNODE;
	vcm->sd.internal_ops = &cn3927_internal_ops;
	vcm->sd.entity.function = MEDIA_ENT_F_LENS;
	ret = media_entity_pads_init(&vcm->sd.entity, 0, NULL);
	if (ret)
		return ret;
	v4l2_ctrl_handler_init(&vcm->ctrls, 1);
	vcm->focus = v4l2_ctrl_new_std(&vcm->ctrls, &cn3927_ctrl_ops,
				    V4L2_CID_FOCUS_ABSOLUTE, 0, 1023, 1,
				    CN3927_DEFAULT_DAC);
	if (vcm->ctrls.error) {
		ret = vcm->ctrls.error;
		goto cleanup;
	}
	/* Writable focus is cached only after verified hardware movement. */
	vcm->focus->flags |= V4L2_CTRL_FLAG_EXECUTE_ON_WRITE;
	vcm->target_dac = CN3927_DEFAULT_DAC;
	vcm->sd.ctrl_handler = &vcm->ctrls;
	pm_runtime_set_suspended(&client->dev);
	pm_runtime_enable(&client->dev);
	ret = pm_runtime_resume_and_get(&client->dev);
	if (ret < 0)
		goto disable_pm;
	ret = pm_runtime_put_sync(&client->dev);
	if (ret < 0)
		goto disable_pm;
	ret = v4l2_async_register_subdev(&vcm->sd);
	if (!ret)
		return 0;
disable_pm:
	pm_runtime_disable(&client->dev);
	if (!pm_runtime_status_suspended(&client->dev))
		cn3927_suspend(&client->dev);
cleanup:
	v4l2_ctrl_handler_free(&vcm->ctrls);
	media_entity_cleanup(&vcm->sd.entity);
	return ret;
}

static void cn3927_remove(struct i2c_client *client)
{
	struct cn3927 *vcm = to_cn3927(i2c_get_clientdata(client));

	v4l2_async_unregister_subdev(&vcm->sd);
	pm_runtime_disable(&client->dev);
	if (!pm_runtime_status_suspended(&client->dev)) {
		cn3927_move(vcm, 0);
		cn3927_write(vcm, 2, 1, 1);
		regulator_bulk_disable(ARRAY_SIZE(vcm->supplies), vcm->supplies);
	}
	v4l2_ctrl_handler_free(&vcm->ctrls);
	media_entity_cleanup(&vcm->sd.entity);
}

static const struct dev_pm_ops cn3927_pm_ops = {
	SET_SYSTEM_SLEEP_PM_OPS(pm_runtime_force_suspend, pm_runtime_force_resume)
	SET_RUNTIME_PM_OPS(cn3927_suspend, cn3927_resume, NULL)
};
static const struct of_device_id cn3927_of_match[] = {
	{ .compatible = "xiaomi,nabu-cn3927" },
	{ }
};
MODULE_DEVICE_TABLE(of, cn3927_of_match);
static struct i2c_driver cn3927_driver = {
	.driver = {
		.name = "nabu-cn3927",
		.of_match_table = cn3927_of_match,
		.pm = &cn3927_pm_ops,
	},
	.probe_new = cn3927_probe,
	.remove = cn3927_remove,
};
module_i2c_driver(cn3927_driver);
MODULE_DESCRIPTION("Xiaomi nabu CN3927 VCM with shared I/O supply management");
MODULE_LICENSE("GPL");
