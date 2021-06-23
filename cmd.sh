# Default config for BUT cluster,

if [ "$(hostname -d)" == "fit.vutbr.cz" ]; then
  queue_conf=$HOME/kaldi_queue_conf/default.long.conf # you have to set your default matylda there! (see example /homes/kazi/iveselyk/queue_conf/default.conf),
  [ ! -e $queue_conf ] && echo "[ERROR] Missing '$queue_conf' !!!" && exit 1
  export feats_cmd="queue.pl --config $queue_conf --mem 2G --matylda 1 --max-jobs-run 50"
  export train_cmd="queue.pl --config $queue_conf --mem 3G --matylda 0.25 --max-jobs-run 200"
  export decode_cmd="queue.pl --config $queue_conf --mem 4G --matylda 0.05 --max-jobs-run 400"
  export cuda_cmd="queue.pl --config $queue_conf --gpu 1 --mem 6G --tmp 10G" # lower --mem to run also on PCO machines ...
else
  export feats_cmd="queue.pl --mem 2G --max-jobs-run 50"
  export train_cmd="queue.pl --mem 3G"
  export decode_cmd="queue.pl --mem 4G"
  export cuda_cmd="queue.pl --gpu 1 --mem 6G"
fi
