import os
import argparse
import matplotlib.pyplot as plt
import numpy as np
from tools.kaldi_io import kaldi_io

parser = argparse.ArgumentParser(description='plot fbank')
parser.add_argument('input_file', type=str, nargs='+', help='input scp file')
parser.add_argument('--outdir', type=str, help='input scp file')
args = parser.parse_args()

out = str(args.outdir)
if not os.path.isdir(out):
    os.mkdir(out)

for scp in args.input_file:
    for key, mat in kaldi_io.read_mat_scp(scp):
        print("The file accesses is " + key)
        name=str(key)
        fig =  plt.figure()
        ax1 = plt.subplot(2, 1, 1)
        ax2 = plt.subplot(2, 1, 2)
        mel_fbank = np.transpose(mat)
        gt_mel_fbank = np.transpose(mat)
        ax1.imshow(mel_fbank, aspect='auto', cmap=plt.cm.jet)
        ax2.imshow(gt_mel_fbank, aspect='auto', cmap=plt.cm.jet)
        fig.savefig('%s/tts.%s.png' % (out,name), orientation='landscape')
