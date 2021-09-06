#!/bin/bash
cd /mnt/matylda6/baskar/espnet_new/egs/dysarthria/asr1
. ./path.sh
( echo '#' Running on `hostname`
  echo '#' Started at `date`
  echo -n '# '; cat <<EOF
asr_recog.py --config conf/decode.yaml --ngpu 0 --backend pytorch --batchsize 0 --recog-json dump/all_speakers/deltafalse/split32utt/data_unigram5000.${SGE_TASK_ID}.json --result-label exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/data.${SGE_TASK_ID}.json --model exp/all_speakers_pytorch_train_specaug/results/model.acc.best --rnnlm exp/train_rnnlm_pytorch_lm_unigram5000/rnnlm.model.best 
EOF
) >exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/log/decode.$SGE_TASK_ID.log
time1=`date +"%s"`
 ( asr_recog.py --config conf/decode.yaml --ngpu 0 --backend pytorch --batchsize 0 --recog-json dump/all_speakers/deltafalse/split32utt/data_unigram5000.${SGE_TASK_ID}.json --result-label exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/data.${SGE_TASK_ID}.json --model exp/all_speakers_pytorch_train_specaug/results/model.acc.best --rnnlm exp/train_rnnlm_pytorch_lm_unigram5000/rnnlm.model.best  ) 2>>exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/log/decode.$SGE_TASK_ID.log >>exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/log/decode.$SGE_TASK_ID.log
ret=$?
time2=`date +"%s"`
echo '#' Accounting: time=$(($time2-$time1)) threads=1 >>exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/log/decode.$SGE_TASK_ID.log
echo '#' Finished at `date` with status $ret >>exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/log/decode.$SGE_TASK_ID.log
[ $ret -eq 137 ] && exit 100;
touch exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/q/sync/done.26547.$SGE_TASK_ID
exit $[$ret ? 1 : 0]
## submitted with:
# qsub -v PATH -cwd -S /bin/bash -j y -l arch=*64* -o exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/q/decode.log -q all.q@blade*   -t 1:32 /mnt/matylda6/baskar/espnet_new/egs/dysarthria/asr1/exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/q/decode.sh >>exp/all_speakers_pytorch_train_specaug/decode_all_speakers_model.acc.best_decode_lm/q/decode.log 2>&1
