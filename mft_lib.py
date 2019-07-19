from scipy.signal import correlate
from multiprocessing import Pool
import numpy as np
import time

def calc_cc(data, temp, **kwargs):

    """ Calc CC trace for a template trace
    Input
        data: continuous data to detect from, np.array
        temp: template data, np.array
    Output
        cc: cross-correlation function
    """

    ntemp = len(temp)
    ndata = len(data)
    if ntemp>ndata: return [0]
    if 'norm_temp' in kwargs: norm_temp = kwargs['norm_temp']
    else: norm_temp = np.sqrt(np.sum(temp**2))
    if 'norm_data' in kwargs: 
        norm_data = kwargs['norm_data']
    if 'norm_data' not in kwargs\
    or len(norm_data)!=ndata-ntemp:
        data_cum = np.cumsum(data**2)
        norm_data = np.sqrt(data_cum[ntemp:] - data_cum[:-ntemp])
    cc = correlate(data, temp, mode='valid')
    cc = cc[1:] /norm_temp /norm_data
    cc[np.isinf(cc)] = 0.
    cc[np.isnan(cc)] = 0.
    return cc


def calc_masked_cci(temp, stream, norm_temp, norm_data, dt, samp_rate, trig_thres, mask_len):
    # calc shifted cc trace
    cci = calc_shifted_cc(temp, stream, norm_temp, norm_data, dt, samp_rate)
    # calc masked cc trace & ppk
    cci, num = mask_cc(cci, trig_thres, mask_len)
    return [cci, num]


def calc_masked_cc(temp_picks, data_dict, trig_thres, mask_len, samp_rate):

    t=time.time()
    out = []
    pool = Pool(processes=len(temp_picks))

    for sta, [temp, norm_temp, _, _, _, dt_ot] in temp_picks.items():
        # get data
        if sta not in data_dict: continue
        [stream, norm_data, date, dt_st] = data_dict[sta]
        # process a station
        outi = pool.apply_async(calc_masked_cci, 
                          args=(temp[0], stream, norm_temp[0], norm_data,
                                dt_st + dt_ot, samp_rate, trig_thres, mask_len))
        out.append([sta, outi])
    pool.close()
    pool.join()
    for [sta, outi] in out: print('{} {} trigs'.format(sta, outi.get()[1]))
    cc = [outi.get()[0] for [_, outi] in out]
    print('process {} stations | time {:.2f}'.format(len(temp_picks), time.time()-t))
    return cc


# 1. calc cc trace & time shift
def calc_shifted_cc(temp_trig, stream, norm_temp, norm_data, dt, samp_rate):

    # calc trig cc
    cci = calc_cc(stream[0].data, temp_trig[0], norm_data=norm_data[0], norm_temp=norm_temp[0])
    cci+= calc_cc(stream[1].data, temp_trig[1], norm_data=norm_data[1], norm_temp=norm_temp[1])
    cci+= calc_cc(stream[2].data, temp_trig[2], norm_data=norm_data[2], norm_temp=norm_temp[2])
    cci = cci / 3.

    # time shift to ot
    cci_holder = np.zeros(int(86400*samp_rate))
    dt = int(samp_rate * dt)
    cci = cci[max(0,-dt) : max(0,-dt) + len(cci)]
    cci_holder[max(0,dt) : max(0,dt) + len(cci)] = cci[0:len(cci_holder)-max(0,dt)]
    return cci_holder


# 2. mask cc trace
def mask_cc(cci, trig_thres, mask_len):

  # cc mask
  trig_idxs = np.where(cci > trig_thres)[0]
  slide_idx = 0
  num=0
  for _ in trig_idxs:
    num+=1

    # mask cci with max cc
    trig_idx = trig_idxs[trig_idxs >= slide_idx][0]
    cc_max = np.amax(cci[trig_idx : trig_idx + mask_len])
    idx_max = np.argmax(cci[trig_idx : trig_idx + mask_len]) + trig_idx
    mask_idx0 = max(0, idx_max - mask_len //2)
    mask_idx1 = idx_max + mask_len//2
    cci[mask_idx0 : mask_idx1] = cc_max

    # next trig
    slide_idx = idx_max + 2*mask_len
    if slide_idx > trig_idxs[-1]: break
  return cci, num


# 3. detect in stacked cc trace
def det_cc_stack(cc_stack, trig_thres, mask_len, date, samp_rate):

  det_idxs = np.where(cc_stack > trig_thres)[0]
  slide_idx = 0
  det_ots = []
  for _ in det_idxs:

    # this detection
    det_idx = det_idxs[det_idxs >= slide_idx][0]
    if det_idx + 2*mask_len > len(cc_stack)-1: break

    # pick ot
    cc_max  = np.amax(cc_stack[det_idx : det_idx + 2*mask_len])
    idx_max = np.where(cc_stack[det_idx: det_idx + 2*mask_len] == cc_max)
    idx_max = int(np.median(idx_max))
    det_oti = date + (det_idx + idx_max) / samp_rate
    det_ots.append([det_oti, cc_max])
    print('detection: ', det_oti, round(cc_max,2))

    # next detection
    slide_idx = idx_max + 2*mask_len
    if slide_idx > det_idxs[-1]: break
  return det_ots


# 4. pick P&S by cc
def ppk_cc(det_oti, temp_picks, data_dict, 
           win_p, win_s, picker, mask_len, samp_rate):

  picksi = []
  for sta, [temp, norm_temp, tp, ts, ot, _] in temp_picks.items():

    # get data
    if sta not in data_dict: continue
    [stream, _, _, dt_st] = data_dict[sta]

    tpi0 = det_oti + (tp - ot)
    tsi0 = det_oti + (ts - ot)

    # cut p&s data
    p_rng = [win_p[0] +   mask_len / samp_rate,
             win_p[1] +   mask_len / samp_rate]
    s_rng = [win_s[0] + 2*mask_len / samp_rate,
             win_s[1] + 2*mask_len / samp_rate]
    st_p = stream.slice(tpi0 - p_rng[0], tpi0 + p_rng[1])
    st_s = stream.slice(tsi0 - s_rng[0], tsi0 + s_rng[1])
    if len(st_p)!=3 or len(st_s)!=3: break

    # ppk by cc
    cc_p  = calc_cc(st_p[2].data, temp[0][2], norm_temp=norm_temp[1][2])
    cc_s0 = calc_cc(st_s[0].data, temp[1][0], norm_temp=norm_temp[2][0])
    cc_s1 = calc_cc(st_s[1].data, temp[1][1], norm_temp=norm_temp[2][1])
    tpi = tpi0 - p_rng[0] + win_p[0] + np.argmax(cc_p) / samp_rate
    tsi = tsi0 - s_rng[0] + win_s[0] + (np.argmax(cc_s0) + np.argmax(cc_s1)) / samp_rate / 2.
    cc_p_max = np.amax(cc_p)
    cc_s_max = (np.amax(cc_s0) + np.amax(cc_s1)) / 2.

    # get S amplitude
    ampx = picker.get_amp(st_s[0].data)
    ampy = picker.get_amp(st_s[1].data)
    ampz = picker.get_amp(st_s[2].data)
    s_amp = np.sqrt(ampx**2 + ampy**2 + ampz**2)

    # add pick
    picksi.append([sta, tpi, tsi, s_amp, cc_p_max, cc_s_max])
  return picksi


def write_det_ppk(det_oti, det_cci, temp_loc, picks, out_ctlg, out_pha):

    # write catalog
    event_line = '{},{},{},{},{:.3f}\n'.\
    format(det_oti, temp_loc[0], temp_loc[1], temp_loc[2], det_cci)
    out_ctlg.write(event_line)
    # write phase
    out_pha.write(event_line)
    for pick in picks:
        out_pha.write('{},{},{},{:.2f},{:.3f},{:.3f}\n'.\
        format(pick[0], pick[1], pick[2], pick[3], pick[4], pick[5]))

