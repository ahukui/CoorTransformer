import torch
import torch.nn as nn
import torch.nn.functional as F


class DoubleConv(nn.Module):
    """(convolution => [BN] => ReLU) * 2"""

    def __init__(self, in_channels, out_channels, mid_channels=None):
        super().__init__()
        if not mid_channels:
            mid_channels = out_channels
        self.double_conv = nn.Sequential(
            nn.Conv2d(in_channels, mid_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(mid_channels),
            nn.ReLU(inplace=True),
            nn.Conv2d(mid_channels, out_channels, kernel_size=3, padding=1),
            nn.BatchNorm2d(out_channels),
            nn.ReLU(inplace=True)
        )

    def forward(self, x):
        return self.double_conv(x)


class Down(nn.Module):
    """Downscaling with maxpool then double conv"""

    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.maxpool_conv = nn.Sequential(
            nn.MaxPool2d(2),
            DoubleConv(in_channels, out_channels)
        )

    def forward(self, x):
        return self.maxpool_conv(x)



class PALayer(nn.Module):
    def __init__(self, channel):
        super(PALayer, self).__init__()
        self.pa = nn.Sequential(
                nn.Conv2d(channel, channel // 8, 1, padding=0, bias=True),
                nn.ReLU(inplace=True),
                nn.Conv2d(channel // 8, 1, 1, padding=0, bias=True),
                nn.Sigmoid()
        )
    def forward(self, x):
        y = self.pa(x)
        return x * y

class CALayer(nn.Module):
    def __init__(self, channel):
        super(CALayer, self).__init__()
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.ca = nn.Sequential(
                nn.Conv2d(channel, channel // 8, 1, padding=0, bias=True),
                nn.ReLU(inplace=True),
                nn.Conv2d(channel // 8, channel, 1, padding=0, bias=True),
                nn.Sigmoid()
        )

    def forward(self, x):
        y = self.avg_pool(x)
        y = self.ca(y)
        return x * y


# ======================================================================================================================
class ResidualBlock(nn.Module):
    def __init__(self, h_dim, norm_layer=None, nl_layer=None, use_dropout=False):
        super(ResidualBlock, self).__init__()
        block = [nn.Conv2d(h_dim, h_dim, 1, padding=0, bias=True)]
        if use_dropout:
            block.append(nn.Dropout(0.5))
        self.encode = nn.Sequential(*block)

        self.calayer = CALayer(h_dim)
        self.palayer=PALayer(h_dim)

    def forward(self, x):
        y = self.encode(x)
        y=self.calayer(y)
        y=self.palayer(y)        
        return x+y




class Up(nn.Module):
    """Upscaling then double conv"""

    def __init__(self, in_channels, out_channels, bilinear=True):
        super().__init__()

        # if bilinear, use the normal convolutions to reduce the number of channels
        if bilinear:
            self.up = nn.Upsample(
                scale_factor=2, mode='bilinear', align_corners=True)
            self.conv = DoubleConv(in_channels, out_channels, in_channels // 2)
        else:
            self.up = nn.ConvTranspose2d(
                in_channels, in_channels // 2, kernel_size=2, stride=2)
            self.conv = DoubleConv(in_channels, out_channels)

    def forward(self, x1, x2):
        x1 = self.up(x1)
        # input is CHW
        diffY = x2.size()[2] - x1.size()[2]
        diffX = x2.size()[3] - x1.size()[3]

        x1 = F.pad(x1, [diffX // 2, diffX - diffX // 2,
                        diffY // 2, diffY - diffY // 2])
        # if you have padding issues, see
        # https://github.com/HaiyongJiang/U-Net-Pytorch-Unstructured-Buggy/commit/0e854509c2cea854e247a9c615f175f76fbb2e3a
        # https://github.com/xiaopeng-liao/Pytorch-UNet/commit/8ebac70e633bac59fc22bb5195e513d5832fb3bd
        x = torch.cat([x2, x1], dim=1)
        return self.conv(x)


class OutConv(nn.Module):
    def __init__(self, in_channels, out_channels):
        super(OutConv, self).__init__()
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size=1)

    def forward(self, x):
        return self.conv(x)


class Encoder(nn.Module):
    def __init__(self, in_channels, out_channels, bilinear=True):
        super(Encoder, self).__init__()
        if not isinstance(in_channels, list):
            in_channels = [in_channels]
        if not isinstance(out_channels, list):
            out_channels = [out_channels]
        self.in_channels = in_channels
        self.bilinear = bilinear

        for i, (n_chan, n_class) in enumerate(zip(in_channels, out_channels)):
            setattr(self, 'in{i}'.format(i=i), OutConv(n_chan, 64))
        self.conv = DoubleConv(64, 64)
        self.down1 = Down(64, 128)
        self.down2 = Down(128, 256)
        self.down3 = Down(256, 512)
        factor = 2 if bilinear else 1
        self.down4 = Down(512, 1024 // factor)
        self.up1 = Up(1024, 512 // factor, bilinear)
        self.up2 = Up(512, 256 // factor, bilinear)
        self.up3 = Up(256, 128 // factor, bilinear)
        self.up4 = Up(128, 64, bilinear)

    def forward(self, x, task_idx=0):
        fea_lsit = []
        x1 = getattr(self, 'in{}'.format(task_idx))(x)
        x1 = self.conv(x1)
        fea_lsit.append(x1)
        x2 = self.down1(x1)
        fea_lsit.append(x2)        
        x3 = self.down2(x2)
        fea_lsit.append(x3)           
        x4 = self.down3(x3)
        fea_lsit.append(x4)          
        x5 = self.down4(x4)
        # x = self.up1(x5, x4)
        # x = self.up2(x, x3)
        # x = self.up3(x, x2)
        # x = self.up4(x, x1)
        # logits = getattr(self, 'out{}'.format(task_idx))(x)
        return x5, fea_lsit#torch.sigmoid(logits)#{'output': torch.sigmoid(logits)}


class UNet(nn.Module):
    def __init__(self, in_channels, out_channels, bilinear=True):
        super(UNet, self).__init__()
        if not isinstance(in_channels, list):
            in_channels = [in_channels]
        if not isinstance(out_channels, list):
            out_channels = [out_channels]
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.bilinear = bilinear

        for i, (n_chan, n_class) in enumerate(zip(in_channels, out_channels)):
            setattr(self, 'in{i}'.format(i=i), OutConv(n_chan, 64))
            setattr(self, 'out{i}'.format(i=i), OutConv(64, n_class))
        self.conv = DoubleConv(64, 64)
        self.down1 = Down(64, 128)
        self.down2 = Down(128, 256)
        self.down3 = Down(256, 512)
        factor = 2 if bilinear else 1
        self.down4 = Down(512, 1024 // factor)
        self.up1 = Up(1024, 512 // factor, bilinear)
        self.up2 = Up(512, 256 // factor, bilinear)
        self.up3 = Up(256, 128 // factor, bilinear)
        self.up4 = Up(128, 64, bilinear)

    def forward(self, x, task_idx=0):
        x1 = getattr(self, 'in{}'.format(task_idx))(x)
        x1 = self.conv(x1)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        x4 = self.down3(x3)
        x5 = self.down4(x4)
        x = self.up1(x5, x4)
        x = self.up2(x, x3)
        x = self.up3(x, x2)
        x = self.up4(x, x1)
        logits = getattr(self, 'out{}'.format(task_idx))(x)
        return {'output': torch.sigmoid(logits)}
