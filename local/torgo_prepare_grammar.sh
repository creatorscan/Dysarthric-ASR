#!/bin/bash

# Copyright 2012 Vassil Panayotov
#           2021 BMK

# Apache 2.0

. path.sh || exit 1

if [ -f path.sh ]; then . ./path.sh; fi
. parse_options.sh || exit 1;

if [ $# -lt 2 ] || [ $# -gt 2 ]; then
   echo "Usage: $0 [options] <spk> <subset>";
   echo "e.g.: $0 F01 single"
   echo "options: "
   exit 1;
fi

spk=$1
subset=$2

local=data/local_$subset
tmp=$(mktemp -d /tmp/${USER}_XXXXX)
lexicon=data/local/dict/lexicon.txt

# Next, for each type of language model, create the corresponding FST
# and the corresponding lang_test_* directory.
echo ""
echo "=== Preparing the grammar transducer (G.fst) for testing ..."
echo ""

test=data/$spk/lang_test_$subset
mkdir -p $test
for f in phones.txt words.txt phones.txt L.fst L_disambig.fst phones; do
    cp -r data/$spk/lang/$f $test
done
cat $local/lm.arpa | \
   utils/find_arpa_oovs.pl $test/words.txt > $tmp/oovs.txt

# grep -v '<s> <s>' because the LM seems to have some strange and useless
# stuff in it with multiple <s>'s in the history.  Encountered some other similar
# things in a LM from Geoff.  Removing all "illegal" combinations of <s> and </s>,
# which are supposed to occur only at being/end of utt.  These can cause 
# determinization failures of CLG [ends up being epsilon cycles].
cat $local/lm.arpa | \
  grep -v '<s> <s>' | \
  grep -v '</s> <s>' | \
  grep -v '</s> </s>' | \
  arpa2fst - | fstprint > $tmp/tmp.fst

# This restricts decoding output to a single word for the 1-word utterances.
if [ "$subset" == "single" ]; then
    sed -i "s/0\t0/0\t1/g" $tmp/tmp.fst
fi

cat $tmp/tmp.fst | \
  utils/remove_oovs.pl $tmp/oovs.txt | \
  utils/eps2disambig.pl | utils/s2eps.pl | fstcompile --isymbols=$test/words.txt \
    --osymbols=$test/words.txt  --keep_isymbols=false --keep_osymbols=false | \
  fstrmepsilon | fstarcsort --sort_type=ilabel > $test/G.fst
fstisstochastic $test/G.fst
# The output is like:
# 9.14233e-05 -0.259833
# we do expect the first of these 2 numbers to be close to zero (the second is
# nonzero because the backoff weights make the states sum to >1).
# Because of the <s> fiasco for these particular LMs, the first number is not
# as close to zero as it could be.

# Everything below is only for diagnostic.
# Checking that G has no cycles with empty words on them (e.g. <s>, </s>);
# this might cause determinization failure of CLG.
# #0 is treated as an empty word.
mkdir -p $tmp/g
awk '{if(NF==1){ printf("0 0 %s %s\n", $1,$1); }} END{print "0 0 #0 #0"; print "0";}' \
  < "$lexicon"  >$tmp/g/select_empty.fst.txt
fstcompile --isymbols=$test/words.txt --osymbols=$test/words.txt \
  $tmp/g/select_empty.fst.txt | \
fstarcsort --sort_type=olabel | fstcompose - $test/G.fst > $tmp/g/empty_words.fst
fstinfo $tmp/g/empty_words.fst | grep cyclic | grep -w 'y' && 
  echo "Language model has cycles with empty words" && exit 1
rm -rf $tmp


