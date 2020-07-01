import oneflow as flow
import numpy as np
import os
import layers
import matplotlib.pyplot as plt


class Pix2Pix:
    def __init__(self, args):
        self.lr = args.learning_rate
        self.out_channels = 3
        self.img_size = 256
        self.LAMBDA = 50
        self.eval_interval = 10

        self.gpus_per_node = args.gpu_num_per_node
        self.batch_size = args.batch_size * self.gpus_per_node
        self.multi_nodes = args.multinode

    def compare_with_tf(self):
        from tf_pix2pix import tf_pix2pix_test
        tf_pix2pix_test()

        func_config = flow.FunctionConfig()
        func_config.default_data_type(flow.float)
        func_config.default_distribute_strategy(flow.distribute.consistent_strategy())
        func_config.train.primary_lr(1e-4)
        func_config.train.model_update_conf(dict(naive_conf={}))

        @flow.global_function(func_config)
        def CompareJob(
            input=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),
            target=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),
        ):
            out = self.generator(input, const_init=True)
            out = self.discriminator(out, target, const_init=True)
            flow.losses.add_loss(out)
            return out

        check_point = flow.train.CheckPoint()
        check_point.init()

        inp = np.load("input.npy").transpose(0, 3, 1, 2).astype(np.float32, order="C")
        target = (
            np.load("target.npy").transpose(0, 3, 1, 2).astype(np.float32, order="C")
        )
        of_out = CompareJob(inp, target).get()
        tf_out = (
            np.load("result.npy").transpose(0, 3, 1, 2).astype(np.float32, order="C")
        )

        print((tf_out - of_out).mean())
        assert np.allclose(of_out.ndarray(), tf_out, rtol=1e-2, atol=1e-2)

    def _downsample(
        self,
        inputs,
        filters,
        size,
        name,
        reuse=False,
        apply_batchnorm=True,
        trainable=True,
        const_init=True,
    ):
        out = layers.conv2d(
            inputs,
            filters,
            size,
            const_init=const_init,
            reuse=reuse,
            trainable=trainable,
            use_bias=False,
            name=name + "_conv",
        )

        if apply_batchnorm:
            out = layers.batchnorm(out, name=name + "_bn", reuse=reuse)

        out = flow.nn.leaky_relu(out, alpha=0.3)
        return out

    def _upsample(
        self,
        inputs,
        filters,
        size,
        name,
        apply_dropout=False,
        trainable=True,
        const_init=True,
    ):

        out = layers.deconv2d(
            inputs,
            filters,
            size,
            const_init=const_init,
            trainable=trainable,
            use_bias=False,
            name=name + "_deconv",
        )

        out = layers.batchnorm(out, name=name + "_bn")

        if apply_dropout:
            out = flow.nn.dropout(out, rate=0.5)

        out = flow.keras.activations.relu(out)
        return out

    def generator(self, inputs, trainable=True, const_init=False):
        if const_init:
            apply_dropout = False
        else:
            apply_dropout = True
        # (n, 64, 128, 128)
        d1 = self._downsample(
            inputs,
            64,
            4,
            const_init=const_init,
            trainable=trainable,
            apply_batchnorm=False,
            name="g_d1",
        )
        # (n, 128, 64, 64)
        d2 = self._downsample(
            d1, 128, 4, const_init=const_init, trainable=trainable, name="g_d2"
        )
        # (n, 256, 32, 32)
        d3 = self._downsample(
            d2, 256, 4, const_init=const_init, trainable=trainable, name="g_d3"
        )
        # (n, 512, 16, 16)
        d4 = self._downsample(
            d3, 512, 4, const_init=const_init, trainable=trainable, name="g_d4"
        )
        # (n, 512, 8, 8)
        d5 = self._downsample(
            d4, 512, 4, const_init=const_init, trainable=trainable, name="g_d5"
        )
        # (n, 512, 4, 4)
        d6 = self._downsample(
            d5, 512, 4, const_init=const_init, trainable=trainable, name="g_d6"
        )
        # (n, 512, 2, 2)
        d7 = self._downsample(
            d6, 512, 4, const_init=const_init, trainable=trainable, name="g_d7"
        )
        # (n, 512, 1, 1)
        d8 = self._downsample(
            d7, 512, 4, const_init=const_init, trainable=trainable, name="g_d8"
        )
        # (n, 1024, 2, 2)
        u7 = self._upsample(
            d8,
            512,
            4,
            const_init=const_init,
            trainable=trainable,
            apply_dropout=apply_dropout,
            name="g_u7",
        )
        u7 = flow.concat([u7, d7], axis=1)
        # (n, 1024, 4, 4)
        u6 = self._upsample(
            u7,
            512,
            4,
            const_init=const_init,
            trainable=trainable,
            apply_dropout=apply_dropout,
            name="g_u6",
        )
        u6 = flow.concat([u6, d6], axis=1)
        # (n, 1024, 8, 8)
        u5 = self._upsample(
            u6,
            512,
            4,
            const_init=const_init,
            trainable=trainable,
            apply_dropout=apply_dropout,
            name="g_u5",
        )
        u5 = flow.concat([u5, d5], axis=1)
        # (n, 1024, 16, 16)
        u4 = self._upsample(
            u5, 512, 4, const_init=const_init, trainable=trainable, name="g_u4"
        )
        u4 = flow.concat([u4, d4], axis=1)
        # (n, 512, 32, 32)
        u3 = self._upsample(
            u4, 256, 4, const_init=const_init, trainable=trainable, name="g_u3"
        )
        u3 = flow.concat([u3, d3], axis=1)
        # (n, 256, 64, 64)
        u2 = self._upsample(
            u3, 128, 4, const_init=const_init, trainable=trainable, name="g_u2"
        )
        u2 = flow.concat([u2, d2], axis=1)
        # (n, 128, 128, 128)
        u1 = self._upsample(
            u2, 64, 4, const_init=const_init, trainable=trainable, name="g_u1"
        )
        u1 = flow.concat([u1, d1], axis=1)
        # (n, 3, 256, 256)
        u0 = layers.deconv2d(
            u1,
            self.out_channels,
            4,
            name="g_u0_deconv",
            const_init=const_init,
            trainable=trainable,
        )
        u0 = flow.keras.activations.tanh(u0)

        return u0

    def discriminator(
        self, inputs, targets, trainable=True, reuse=False, const_init=False
    ):
        # (n, 6, 256, 256)
        d0 = flow.concat([inputs, targets], axis=1)
        # (n, 64, 128, 128)
        d1 = self._downsample(
            d0,
            64,
            4,
            name="d_d1",
            apply_batchnorm=False,
            reuse=reuse,
            const_init=const_init,
        )
        # (n, 64, 64, 64)
        d2 = self._downsample(
            d1, 128, 4, name="d_d2", reuse=reuse, const_init=const_init
        )
        # (n, 256, 32, 32)
        d3 = self._downsample(
            d2, 256, 4, name="d_d3", reuse=reuse, const_init=const_init
        )
        # (n, 256, 34, 34)
        pad1 = flow.pad(d3, [[0, 0], [0, 0], [1, 1], [1, 1]])
        # (n, 512, 31, 31)
        conv1 = layers.conv2d(
            pad1,
            512,
            4,
            strides=1,
            padding="valid",
            name="d_conv1",
            trainable=trainable,
            reuse=reuse,
            const_init=const_init,
            use_bias=False,
        )
        bn1 = layers.batchnorm(conv1, name="d_bn1", reuse=reuse)
        leaky_relu = flow.nn.leaky_relu(bn1, alpha=0.3)
        # (n, 512, 33, 33)
        pad2 = flow.pad(leaky_relu, [[0, 0], [0, 0], [1, 1], [1, 1]])
        # (n, 1, 30, 30)
        conv2 = layers.conv2d(
            pad2,
            1,
            4,
            strides=1,
            padding="valid",
            name="d_conv2",
            trainable=trainable,
            reuse=reuse,
            const_init=const_init,
        )

        return conv2

    def load_facades(self, mode="train"):
        from PIL import Image
        if not os.path.exists("data/facades/"):
            print("not Found Facades - start download")
            import tensorflow as tf
            if not os.path.exists("data"):
                os.mkdir("data")
            _PATH = os.path.join(os.getcwd(), "data/facades.tar.gz")
            _URL = "https://people.eecs.berkeley.edu/~tinghuiz/projects/pix2pix/datasets/facades.tar.gz"
            path_to_zip = tf.keras.utils.get_file(_PATH, origin=_URL, extract=True)

        input_imgs, real_imgs = [], []
        for d in os.listdir(os.path.join("data/facades/", mode)):
            d = os.path.join("data/facades/", mode, d)
            img = np.asarray(Image.open(d))
            real_img = img[:, :256, :]
            input_img = img[:, 256:, :]
            input_imgs.append(input_img)
            real_imgs.append(real_img)

        input_imgs = np.array(input_imgs).transpose(0, 3, 1, 2)
        real_imgs = np.array(real_imgs).transpose(0, 3, 1, 2)
        input_imgs = input_imgs / 127.5 - 1
        real_imgs = real_imgs / 127.5 - 1
        return input_imgs, real_imgs

    def _eval_model(self, model, batch_idx, epoch_idx):
        ind = 3
        input, target = self.load_facades(mode="test")
        gout = model(input[ind * self.batch_size : (ind + 1) * self.batch_size].astype(np.float32, order="C")).get()
        plt.figure(figsize=(15, 15))

        display_list = [input[ind], target[ind], gout[0]]
        title = ["Input Image", "Ground Truth", "Predicted Image"]

        for i in range(3):
            plt.subplot(1, 3, i + 1)
            plt.title(title[i])
            # getting the pixel values between [0, 1] to plot it.
            plt.imshow(display_list[i].transpose(1, 2, 0) * 0.5 + 0.5)
            plt.axis("off")
        if not os.path.exists("gout"):
            os.mkdir("gout")
        plt.savefig("gout/image_{:02d}_{:04d}.png".format(epoch_idx + 1, batch_idx + 1))
        plt.close()

    def test(self):
        func_config = flow.FunctionConfig()
        func_config.default_data_type(flow.float)
        func_config.default_distribute_strategy(flow.distribute.consistent_strategy())

        @flow.global_function(func_config)
        def eval_generator(input=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),):
            g_out = self.generator(input, trainable=False)
            return g_out

        batch_idx = 1
        epoch_idx = 1
        check_point = flow.train.CheckPoint()
        check_point.init()
        x, y = self.load_facades()
        batch_num = len(x) // self.batch_size
        inp = x[batch_idx * self.batch_size : (batch_idx + 1) * self.batch_size].astype(
            np.float32, order="C"
        )
        target = y[
            batch_idx * self.batch_size : (batch_idx + 1) * self.batch_size
        ].astype(np.float32, order="C")
        self._eval_model(eval_generator, batch_idx, epoch_idx)

    def train(self, epochs=1, save=True):
        func_config = flow.FunctionConfig()
        func_config.default_data_type(flow.float)
        func_config.default_distribute_strategy(flow.distribute.consistent_strategy())
        func_config.train.primary_lr(self.lr)
        func_config.train.model_update_conf(dict(adam_conf={"beta1": 0.5}))
        flow.config.gpu_device_num(self.gpus_per_node)
        if self.multi_nodes:
            self.init_nodes()

        @flow.global_function(func_config)
        def train_generator(
            input=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),
            target=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),
            label1=flow.FixedTensorDef((self.batch_size, 1, 30, 30)),
        ):
            g_out = self.generator(input, trainable=True)
            g_logits = self.discriminator(input, g_out, trainable=False)
            gan_loss = flow.nn.sigmoid_cross_entropy_with_logits(
                label1, g_logits, name="Gloss_sigmoid_cross_entropy_with_logits"
            )
            l1_loss = flow.math.reduce_mean(flow.math.abs(g_out - target))
            g_loss = gan_loss + self.LAMBDA * l1_loss

            flow.losses.add_loss(g_loss)
            return g_loss, g_out

        @flow.global_function(func_config)
        def train_discriminator(
            input=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),
            target=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),
            label0=flow.FixedTensorDef((self.batch_size, 1, 30, 30)),
            label1=flow.FixedTensorDef((self.batch_size, 1, 30, 30)),
        ):
            g_out = self.generator(input, trainable=False)
            g_logits = self.discriminator(g_out, target, trainable=True)
            d_fake_loss = flow.nn.sigmoid_cross_entropy_with_logits(
                label0, g_logits, name="Dloss_fake_sigmoid_cross_entropy_with_logits"
            )

            d_logits = self.discriminator(input, target, trainable=True, reuse=True)
            d_real_loss = flow.nn.sigmoid_cross_entropy_with_logits(
                label1, d_logits, name="Dloss_real_sigmoid_cross_entropy_with_logits"
            )

            d_loss = d_fake_loss + d_real_loss

            flow.losses.add_loss(d_loss)
            return d_loss

        func_config = flow.FunctionConfig()
        func_config.default_data_type(flow.float)
        func_config.default_distribute_strategy(flow.distribute.consistent_strategy())

        @flow.global_function(func_config)
        def eval_generator(input=flow.FixedTensorDef((self.batch_size, 3, 256, 256)),):
            g_out = self.generator(input, trainable=False)
            return g_out

        check_point = flow.train.CheckPoint()
        check_point.init()

        x, y = self.load_facades()
        batch_num = len(x) // self.batch_size
        print(batch_num)

        for epoch_idx in range(epochs):
            for batch_idx in range(batch_num):
                label1 = np.ones((self.batch_size, 1, 30, 30)).astype(np.float32)
                label0 = np.zeros((self.batch_size, 1, 30, 30)).astype(np.float32)
                inp = x[
                    batch_idx * self.batch_size : (batch_idx + 1) * self.batch_size
                ].astype(np.float32, order="C")
                target = y[
                    batch_idx * self.batch_size : (batch_idx + 1) * self.batch_size
                ].astype(np.float32, order="C")
                d_loss = train_discriminator(inp, target, label0, label1).get()
                g_loss, _ = train_generator(inp, target, label1).get()
                if (batch_idx + 1) % self.eval_interval == 0:
                    print(
                        "{}th epoch, {}th batch, dloss:{:>12.6f}, gloss:{:>12.6f}".format(
                            epoch_idx + 1, batch_idx + 1, d_loss.mean(), g_loss.mean()
                        )
                    )
                    self._eval_model(eval_generator, batch_idx, epoch_idx)

        if save:
            from datetime import datetime

            if not os.path.exists("checkpoint"):
                os.mkdir("checkpoint")
            check_point.save(
                "checkpoint/cp_{}".format(
                    str(datetime.now().strftime("%Y-%m-%d-%H:%M:%S"))
                )
            )


if __name__ == "__main__":
    os.environ["ENABLE_USER_OP"] = "True"
    import argparse
    parser = argparse.ArgumentParser(description="flags for multi-node and resource")
    parser.add_argument("-g", "--gpu_num_per_node", type=int, default=4, required=False)
    parser.add_argument("-e", "--epoch_num", type=int, default=10, required=False)
    parser.add_argument("-lr", "--learning_rate", type=float, default=1e-4, required=False)
    parser.add_argument(
        "-c", "--compare", default=False, action="store_true", required=False
    )
    parser.add_argument(
        "-m", "--multinode", default=False, action="store_true", required=False
    )
    parser.add_argument(
        "-load", "--model_load_dir", type=str, default="checkpoint", required=False
    )
    parser.add_argument(
        "-save", "--model_save_dir", type=str, default="checkpoint", required=False
    )
    parser.add_argument("-b", "--batch_size", type=int, default=1, required=False)
    args = parser.parse_args()
    pix2pix = Pix2Pix(args)
    if args.compare:
        pix2pix.compare_with_tf()
    pix2pix.train(epochs=args.epoch_num)
