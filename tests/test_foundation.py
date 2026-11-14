"""Channel adaptation and head shapes for scripts/detect/foundation.py (no downloads)."""
import torch

from scripts.detect import foundation as fd


def test_adapt_in_channels_zero_keeps_pretrained():
    w = torch.randn(8, 1, 4, 4)
    a = fd.adapt_in_channels(w, 2, "zero")
    assert a.shape == (8, 2, 4, 4)
    assert torch.equal(a[:, :1], w) and torch.all(a[:, 1] == 0)
    x = torch.randn(1, 1, 8, 8)
    hv = torch.randn(1, 1, 8, 8)
    y1 = torch.nn.functional.conv2d(x, w, stride=4)
    y2 = torch.nn.functional.conv2d(torch.cat([x, hv], 1), a, stride=4)
    assert torch.allclose(y1, y2, atol=1e-6)  # HV does not change the pretrained output at init


def test_adapt_in_channels_mean_scale():
    w = torch.randn(4, 2, 3, 3)
    a = fd.adapt_in_channels(w, 4, "mean")
    assert a.shape == (4, 4, 3, 3)
    x = torch.randn(1, 1, 5, 5)
    y_old = torch.nn.functional.conv2d(x.repeat(1, 2, 1, 1), w)
    y_new = torch.nn.functional.conv2d(x.repeat(1, 4, 1, 1), a)
    assert torch.allclose(y_old, y_new, atol=1e-5)


def test_pseudo_db_and_change_rule():
    x = torch.full((2, 2, 6, 6), 0.6)
    water = torch.zeros(6, 6, dtype=torch.bool)
    water[:2] = True
    x[0, :, :2] = 0.05            # reference: water in the top two rows
    x[1, :, :2] = 0.05
    x[1, :, 2:4] = 0.05           # later date: rows 2-3 became water
    db, coef = fd.preview_to_pseudo_db(x, water)
    assert abs(db[0, 0, 0, 0].item() - (-22.0)) < 1e-4 and abs(db[0, 1, 0, 0].item() - (-28.0)) < 1e-4
    assert coef[0][0] == 30.0
    ch = fd.ai4g_change(db[0:1].expand(2, -1, -1, -1), db)
    assert ch.shape == (2, 2, 6, 6)
    assert ch[0].sum() == 0                       # no change against itself
    assert torch.all(ch[1, :, 2:4] == 1)          # new water in both channels
    assert torch.all(ch[1, :, :2] == 0) and torch.all(ch[1, :, 4:] == 0)


def test_ai4g_unet_shapes():
    m = fd.AI4GFloodUNet().eval()
    with torch.no_grad():
        y = m(torch.zeros(1, 2, 128, 128))
    assert y.shape == (1, 2, 128, 128)
    keys = m.state_dict().keys()
    assert "encoder.features.0.0.weight" in keys and "decoder.blocks.4.conv2.1.running_var" in keys
    assert "segmentation_head.0.weight" in keys


def test_swin_encoder_and_head_shapes():
    enc = fd.SwinEncoder(img=64, in_chans=2, embed=16, depths=(2, 2, 2, 2), heads=(1, 2, 4, 8), window=4).eval()
    head = fd.LightFPNHead(enc.dims, classes=3, width=8).eval()
    with torch.no_grad():
        feats = enc(torch.zeros(2, 2, 64, 64))
        out = head(feats)
    assert [f.shape[1] for f in feats] == [16, 32, 64, 128]
    assert [f.shape[-1] for f in feats] == [16, 8, 4, 2]
    assert out.shape == (2, 3, 64, 64)
    assert "layers.2.blocks.1.attn.relative_position_bias_table" in enc.state_dict()


def test_alos2_normalise():
    assert abs(fd.alos2_normalise(torch.tensor(fd.ALOS2_MEAN_DB)).item()) < 1e-6
