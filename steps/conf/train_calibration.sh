#!/bin/bash
# Copyright 2015, Brno University of Technology (Author: Karel Vesely). Apache 2.0.

# Trains logistic regression, which calibrates the per-word confidences in 'CTM'.
# The 'raw' confidences are obtained by Minimum Bayes Risk decoding.

# The input features of logistic regression are:
# - logit of Minumum Bayer Risk posterior
# - log of word-length in characters
# - log of average-depth depth of a lattice at words' position
# - log of frames per character ratio
# (- categorical distribution of 'lang/words.txt', DISABLED)

# begin configuration section.
cmd=
lmwt=10
decode_mbr=true
word_min_count=10 # Minimum word-count for single-word category,
normalizer=0.0025 # L2 regularization constant,
normalizer=0.025 # L2 regularization constant,
category_text= # Alternative corpus for counting words to get word-categories (by default using 'ctm'),
stage=0
# end configuration section.

[ -f ./path.sh ] && . ./path.sh
. parse_options.sh || exit 1;

if [ $# -ne 5 ]; then
  echo "Usage: $0 [opts] <data-dir> <lang-dir|graph-dir> <word-feats> <decode-dir> <calibration-dir>"
  echo " Options:"
  echo "    --cmd (run.pl|queue.pl...)      # specify how to run the sub-processes."
  echo "    --lmwt <int>                    # scaling for confidence extraction"
  echo "    --decode-mbr <bool>             # use Minimum Bayes Risk decoding"
  echo "    --grep-filter <str>             # remove words from calibration targets"
  exit 1;
fi

set -euo pipefail

data=$1
lang=$2 # Note: may be graph directory not lang directory, but has the necessary stuff copied.
word_feats=$3
latdir=$4
dir=$5

model=$latdir/../final.mdl # assume model one level up from decoding dir.

for f in $lang/words.txt $word_feats $latdir/lat.1.gz; do
  [ ! -f $f ] && echo "$0: Missing file $f" && exit 1
done
if [ ! -f $data/text ]; then
    echo "$0: Missing file $data/text"
elif [ ! -f $data/stm ]; then
    echo "$0: Missing file $data/stm"
else
    echo "$0: Missing file and $data/text and $data/stm" 
fi
[ -z "$cmd" ] && echo "$0: Missing --cmd '...'" && exit 1

[ -d $dir/log ] || mkdir -p $dir/log
nj=$(cat $latdir/num_jobs)

# Store the setup,
echo $lmwt >$dir/lmwt
echo $decode_mbr >$dir/decode_mbr
cp $word_feats $dir/word_feats

# Create the ctm with raw confidences,
# - we keep the timing relative to the utterance,
if [ $stage -le 0 ]; then
  $cmd JOB=1:$nj $dir/log/get_ctm.JOB.log \
    lattice-scale --inv-acoustic-scale=$lmwt "ark:gunzip -c $latdir/lat.JOB.gz|" ark:- \| \
    lattice-limit-depth ark:- ark:- \| \
    lattice-push --push-strings=false ark:- ark:- \| \
    lattice-align-words-lexicon --max-expand=10.0 \
     $lang/phones/align_lexicon.int $model ark:- ark:- \| \
    lattice-to-ctm-conf --decode-mbr=$decode_mbr ark:- - \| \
    utils/int2sym.pl -f 5 $lang/words.txt \| \
    utils/convert_ctm.pl $data/segments $data/reco2file_and_channel \
    '>' $dir/JOB.ctm
  # Merge and clean,
  for ((n=1; n<=nj; n++)); do cat $dir/${n}.ctm; done > $dir/ctm
  rm $dir/*.ctm
fi
exit 0
# Get evaluation of the 'ctm' using the 'text' reference,
if [ $stage -le 1 ]; then
#   false && {
#  if [ -f $data/text ]; then
#      steps/conf/convert_ctm_to_tra.py $dir/ctm - | \
#      align-text --special-symbol="<eps>" ark:$data/text ark:- ark,t:- | \
#      utils/scoring/wer_per_utt_details.pl --special-symbol "<eps>" \
#      >$dir/align_text 
#  elif [ -f $data/stm ]; then 
#      grep -v ";;" $data/stm > $data/stm.filt
#
#      cut -f 7- -d " " $data/stm.filt | sed "s| |@|g" > $data/stm.filt.tmp1
#      awk '{print $1 " " $2 " " $3 " " $4 " " $5 " " $6}'  $data/stm.filt > $data/stm.filt.tmp2
#      paste $data/stm.filt.tmp2 $data/stm.filt.tmp1 | sed "s|	| |g" > $data/stm.filt.reform
#      steps/conf/convert_stm_to_tra.py $data/stm.filt.reform $data/text_from_stm
#      sed -i "s|@| |g" $data/text_from_stm
#      steps/conf/convert_ctm_to_tra.py $dir/ctm $data/text_from_ctm
#     # | \
#      align-text --special-symbol="<eps>" ark:$data/text_from_stm ark:$data/text_from_ctm ark,t:- | \
#      utils/scoring/wer_per_utt_details.pl --special-symbol "<eps>" \
#      >$dir/align_text 
#  fi      
#}
  # Append alignment to ctm,
  #steps/conf/append_eval_to_ctm.py $dir/align_text $dir/ctm $dir/ctm_aligned
  steps/conf/append_prf_to_ctm.py $dir/../scoring_${lmwt}/ctm_filt_stm.prf $dir/../scoring_${lmwt}/ctm_filt $dir/ctm_aligned
  # Convert words to 'ids',
  cat $dir/ctm_aligned | utils/sym2int.pl -f 5 $lang/words.txt >$dir/ctm_aligned_int
fi

# Prepare word-categories (based on wotd frequencies in 'ctm'),
if [ -z "$category_text" ]; then
  steps/conf/convert_ctm_to_tra.py $dir/ctm - | \
  steps/conf/prepare_word_categories.py --min-count $word_min_count $lang/words.txt - $dir/word_categories
else
  steps/conf/prepare_word_categories.py --min-count $word_min_count $lang/words.txt "$category_text" $dir/word_categories
fi

# Compute lattice-depth,
#latdepth=$dir/lattice_frame_depth.ark
#if [ $stage -le 2 ]; then
#  [ -e $latdepth ] || steps/conf/lattice_depth_per_frame.sh --cmd "$cmd" $latdir $dir
#fi

# Create the training data for logistic regression,
if [ $stage -le 3 ]; then
  steps/conf/prepare_calibration_data.py \
    --conf-targets $dir/train_targets.ark --conf-feats $dir/train_feats.ark \
    $dir/ctm_aligned_int $word_feats $dir/word_categories
    #--lattice-depth $latdepth $dir/ctm_aligned_int $word_feats $dir/word_categories
fi

# Train the logistic regression,
if [ $stage -le 4 ]; then
  logistic-regression-train --binary=false --normalizer=$normalizer ark:$dir/train_feats.ark \
    ark:$dir/train_targets.ark $dir/calibration.mdl 2>$dir/log/logistic-regression-train.log
fi

# Apply calibration model to dev,
if [ $stage -le 5 ]; then
  logistic-regression-eval --apply-log=false $dir/calibration.mdl \
    ark:$dir/train_feats.ark ark,t:- | \
    awk '{ key=$1; p_corr=$4; sub(/,.*/,"",key); gsub(/\^/," ",key); print key,p_corr }' | \
    utils/int2sym.pl -f 5 $lang/words.txt \
    >$dir/ctm_calibrated_int
fi

exit 0
