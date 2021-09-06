. ./path.sh || exit 1;
. ./cmd.sh || exit 1;

nj=5000
data=$1
dump=$2

echo "script <datadir> <dumpdir>"

mfccdir=mfcc
vaddir=mfcc
        if [ ! -s ${data}_mfcc_16k/feats.scp ]; then
        utils/copy_data_dir.sh ${data} ${data}_mfcc_16k
        utils/data/resample_data_dir.sh 16000 ${data}_mfcc_16k
        steps/make_mfcc.sh \
            --write-utt2num-frames true \
            --mfcc-config conf/mfcc.conf \
            --nj ${nj} --cmd "$train_cmd" \
            ${data}_mfcc_16k exp/make_mfcc_16k ${mfccdir}
        utils/fix_data_dir.sh ${data}_mfcc_16k
        sid/compute_vad_decision.sh --nj ${nj} --cmd "$train_cmd" \
            ${data}_mfcc_16k exp/make_vad ${vaddir}
        utils/fix_data_dir.sh ${data}_mfcc_16k
        fi
    # Check pretrained model existence
    nnet_dir=exp/xvector_nnet_1a
    # Extract x-vector
    sid/nnet3/xvector/extract_xvectors.sh --cmd "$train_cmd --mem 4G" --nj 500 \
            ${nnet_dir} ${data}_mfcc_16k \
            ${nnet_dir}/xvectors_dev_indian
    # Update json
    local/update_json.sh $dump/data_tts.json ${nnet_dir}/xvectors_dev_indian/xvector.scp
