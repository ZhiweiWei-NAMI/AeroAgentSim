from .outage_callback import OutageProbCallback
from .pathloss_callback import PathLossCallback
from .shadowing_callback import ShadowingCallback
from .fading_callback import FastFadingCallback


def addMatrix(add_ma, value, add_mb, rb_nos, txidx, rxidx, inverse=False):
    # 判断txidx和rxidx是否都在ma和mb的范围内
    flag1 = txidx < add_ma.shape[0] and rxidx < add_ma.shape[1]
    flag2 = (txidx < add_mb.shape[0] and rxidx < add_mb.shape[1] and not inverse) or (txidx < add_mb.shape[1] and rxidx < add_mb.shape[0] and inverse)
    if flag1 and flag2:
        if inverse:
            increment = 10 ** ((value - add_mb[rxidx, txidx, :]) / 10) * rb_nos
        else:
            increment = 10 ** ((value - add_mb[txidx, rxidx, :]) / 10) * rb_nos
        # 更新 add_ma
        add_ma[txidx, rxidx, :] += increment

# (interference_power_matrix_utx_x2v, self.U2V_power_dB, self.V2UChannel_with_fastfading, rb_nos, txidx, rxidx)
def subMatrix(sub_ma, value, sub_mb, rb_nos, txidx, rxidx, inverse=False):
    # 判断txidx和rxidx是否都在ma和mb的范围内
    flag1 = txidx < sub_ma.shape[0] and rxidx < sub_ma.shape[1]
    flag2 = (txidx < sub_mb.shape[0] and rxidx < sub_mb.shape[1] and not inverse) or (txidx < sub_mb.shape[1] and rxidx < sub_mb.shape[0] and inverse)
    if flag1 and flag2:
        if inverse:
            decrement = 10 ** ((value - sub_mb[rxidx, txidx, :]) / 10) * rb_nos
        else:
            decrement = 10 ** ((value - sub_mb[txidx, rxidx, :]) / 10) * rb_nos
        sub_ma[txidx, rxidx, :] -= decrement

def addTwoMatrix(add_ma, value, add_mb, rb_nos, txidx, inverse=False):
    # interference_power_matrix_vtx_x2i[txidx, :, :] += 10 ** ((power_db - self.V2IChannel_with_fastfading[txidx, :, :]) / 10) * rb_nos
    flag1 = txidx < add_ma.shape[0]
    flag2 = txidx < add_mb.shape[0] and not inverse or txidx < add_mb.shape[1] and inverse
    if flag1 and flag2:
        if inverse:
            increment = 10 ** ((value - add_mb[:, txidx, :]) / 10) * rb_nos
        else:
            increment = 10 ** ((value - add_mb[txidx, :, :]) / 10) * rb_nos
        add_ma[txidx, :, :] += increment
