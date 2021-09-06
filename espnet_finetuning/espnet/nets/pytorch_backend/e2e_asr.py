#!/usr/bin/env python3

# Copyright 2017 Johns Hopkins University (Shinji Watanabe)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)


from __future__ import division

import argparse
import logging
import math
import os

import editdistance

import chainer
import numpy as np
import six
import torch

from itertools import groupby

from chainer import reporter

from espnet.nets.asr_interface import ASRInterface
from espnet.nets.e2e_asr_common import label_smoothing_dist
from espnet.nets.pytorch_backend.ctc import ctc_for
from espnet.nets.pytorch_backend.nets_utils import pad_list
from espnet.nets.pytorch_backend.nets_utils import to_device
from espnet.nets.pytorch_backend.nets_utils import to_torch_tensor
from espnet.nets.pytorch_backend.rnn.attentions import att_for
from espnet.nets.pytorch_backend.rnn.decoders import decoder_for
from espnet.nets.pytorch_backend.rnn.encoders import encoder_for
from espnet.nets.scorers.ctc import CTCPrefixScorer

CTC_LOSS_THRESHOLD = 10000


class Reporter(chainer.Chain):
    """A chainer reporter wrapper"""

    def report(self, loss_ctc, loss_att, acc, cer_ctc, cer, wer, mtl_loss):#, loss_tts):
        reporter.report({'loss_ctc': loss_ctc}, self)
        reporter.report({'loss_att': loss_att}, self)
        reporter.report({'acc': acc}, self)
        reporter.report({'cer_ctc': cer_ctc}, self)
        reporter.report({'cer': cer}, self)
        reporter.report({'wer': wer}, self)
        logging.info('mtl loss:' + str(mtl_loss))
        reporter.report({'loss': mtl_loss}, self)
        #reporter.report({'loss_tts': loss_tts}, self)


class E2E(ASRInterface, torch.nn.Module):
    """E2E module

    :param int idim: dimension of inputs
    :param int odim: dimension of outputs
    :param Namespace args: argument Namespace containing options

    """
    @staticmethod
    def add_arguments(parser):
        E2E.encoder_add_arguments(parser)
        E2E.attention_add_arguments(parser)
        E2E.decoder_add_arguments(parser)
        return parser

    @staticmethod
    def encoder_add_arguments(parser):
        group = parser.add_argument_group("E2E encoder setting")
        # encoder
        group.add_argument('--etype', default='blstmp', type=str,
                           choices=['lstm', 'blstm', 'lstmp', 'blstmp', 'vgglstmp', 'vggblstmp', 'vgglstm', 'vggblstm',
                                    'gru', 'bgru', 'grup', 'bgrup', 'vgggrup', 'vggbgrup', 'vgggru', 'vggbgru'],
                           help='Type of encoder network architecture')
        group.add_argument('--elayers', default=4, type=int,
                           help='Number of encoder layers (for shared recognition part in multi-speaker asr mode)')
        group.add_argument('--eunits', '-u', default=300, type=int,
                           help='Number of encoder hidden units')
        group.add_argument('--eprojs', default=320, type=int,
                           help='Number of encoder projection units')
        group.add_argument('--subsample', default="1", type=str,
                           help='Subsample input frames x_y_z means subsample every x frame at 1st layer, '
                                'every y frame at 2nd layer etc.')
        return parser

    @staticmethod
    def attention_add_arguments(parser):
        group = parser.add_argument_group("E2E attention setting")
        # attention
        group.add_argument('--atype', default='dot', type=str,
                           choices=['noatt', 'dot', 'add', 'location', 'coverage',
                                    'coverage_location', 'location2d', 'location_recurrent',
                                    'multi_head_dot', 'multi_head_add', 'multi_head_loc',
                                    'multi_head_multi_res_loc'],
                           help='Type of attention architecture')
        group.add_argument('--adim', default=320, type=int,
                           help='Number of attention transformation dimensions')
        group.add_argument('--awin', default=5, type=int,
                           help='Window size for location2d attention')
        group.add_argument('--aheads', default=4, type=int,
                           help='Number of heads for multi head attention')
        group.add_argument('--aconv-chans', default=-1, type=int,
                           help='Number of attention convolution channels \
                           (negative value indicates no location-aware attention)')
        group.add_argument('--aconv-filts', default=100, type=int,
                           help='Number of attention convolution filters \
                           (negative value indicates no location-aware attention)')
        group.add_argument('--dropout-rate', default=0.0, type=float,
                           help='Dropout rate for the encoder')
        return parser

    @staticmethod
    def decoder_add_arguments(parser):
        group = parser.add_argument_group("E2E encoder setting")
        group.add_argument('--dtype', default='lstm', type=str,
                           choices=['lstm', 'gru'],
                           help='Type of decoder network architecture')
        group.add_argument('--dlayers', default=1, type=int,
                           help='Number of decoder layers')
        group.add_argument('--dunits', default=320, type=int,
                           help='Number of decoder hidden units')
        group.add_argument('--dropout-rate-decoder', default=0.0, type=float,
                           help='Dropout rate for the decoder')
        group.add_argument('--sampling-probability', default=0.0, type=float,
                           help='Ratio of predicted labels fed back to decoder')
        return parser

    def __init__(self, idim, odim, args):
        super(E2E, self).__init__()
        torch.nn.Module.__init__(self)
        self.mtlalpha = args.mtlalpha
        assert 0.0 <= self.mtlalpha <= 1.0, "mtlalpha should be [0.0, 1.0]"
        self.etype = args.etype
        self.verbose = args.verbose
        # NOTE: for self.build method
        args.char_list = getattr(args, "char_list", None)
        self.char_list = args.char_list
        self.outdir = args.outdir
        self.space = args.sym_space
        self.blank = args.sym_blank
        self.oracle_length = args.oracle_length
        self.reporter = Reporter()

        # below means the last number becomes eos/sos ID
        # note that sos/eos IDs are identical
        self.sos = odim - 1
        self.eos = odim - 1

        # subsample info
        # +1 means input (+1) and layers outputs (args.elayer)
        subsample = np.ones(args.elayers + 1, dtype=np.int)
        if args.etype.endswith("p") and not args.etype.startswith("vgg"):
            ss = args.subsample.split("_")
            for j in range(min(args.elayers + 1, len(ss))):
                subsample[j] = int(ss[j])
        else:
            logging.warning(
                'Subsampling is not performed for vgg*. It is performed in max pooling layers at CNN.')
        logging.info('subsample: ' + ' '.join([str(x) for x in subsample]))
        self.subsample = subsample

        # label smoothing info
        if args.lsm_type and os.path.isfile(args.train_json):
            logging.info("Use label smoothing with " + args.lsm_type)
            labeldist = label_smoothing_dist(odim, args.lsm_type, transcript=args.train_json)
        else:
            labeldist = None

        # speech translation related
        self.replace_sos = getattr(args, "replace_sos", False)  # use getattr to keep compatibility

        if getattr(args, "use_frontend", False):  # use getattr to keep compatibility
            # Relative importing because of using python3 syntax
            from espnet.nets.pytorch_backend.frontends.feature_transform \
                import feature_transform_for
            from espnet.nets.pytorch_backend.frontends.frontend \
                import frontend_for

            self.frontend = frontend_for(args, idim)
            self.feature_transform = feature_transform_for(args, (idim - 1) * 2)
            idim = args.n_mels
        else:
            self.frontend = None

        # encoder
        self.enc = encoder_for(args, idim, self.subsample)
        # ctc
        self.ctc = ctc_for(args, odim)
        # attention
        self.att = att_for(args)
        # decoder
        self.dec = decoder_for(args, odim, self.sos, self.eos, self.att, labeldist)

        # weight initialization
        self.init_like_chainer()

        # options for beam search
        if args.report_cer or args.report_wer:
            recog_args = {'beam_size': args.beam_size, 'penalty': args.penalty,
                          'ctc_weight': args.ctc_weight, 'maxlenratio': args.maxlenratio,
                          'minlenratio': args.minlenratio, 'lm_weight': args.lm_weight,
                          'rnnlm': args.rnnlm, 'nbest': args.nbest,
                          'space': args.sym_space, 'blank': args.sym_blank,
                          'tgt_lang': False, 'sampling': args.sampling}

            self.recog_args = argparse.Namespace(**recog_args)
            self.report_cer = args.report_cer
            self.report_wer = args.report_wer
        else:
            self.report_cer = False
            self.report_wer = False
        self.rnnlm = None

        self.logzero = -10000000000.0
        self.loss = None
        self.acc = None
        self.loss_nll = torch.nn.NLLLoss()

    def init_like_chainer(self):
        """Initialize weight like chainer

        chainer basically uses LeCun way: W ~ Normal(0, fan_in ** -0.5), b = 0
        pytorch basically uses W, b ~ Uniform(-fan_in**-0.5, fan_in**-0.5)

        however, there are two exceptions as far as I know.
        - EmbedID.W ~ Normal(0, 1)
        - LSTM.upward.b[forget_gate_range] = 1 (but not used in NStepLSTM)
        """

        def lecun_normal_init_parameters(module):
            for p in module.parameters():
                data = p.data
                if data.dim() == 1:
                    # bias
                    data.zero_()
                elif data.dim() == 2:
                    # linear weight
                    n = data.size(1)
                    stdv = 1. / math.sqrt(n)
                    data.normal_(0, stdv)
                elif data.dim() in (3, 4):
                    # conv weight
                    n = data.size(1)
                    for k in data.size()[2:]:
                        n *= k
                    stdv = 1. / math.sqrt(n)
                    data.normal_(0, stdv)
                else:
                    raise NotImplementedError

        def set_forget_bias_to_one(bias):
            n = bias.size(0)
            start, end = n // 4, n // 2
            bias.data[start:end].fill_(1.)

        lecun_normal_init_parameters(self)
        # exceptions
        # embed weight ~ Normal(0, 1)
        self.dec.embed.weight.data.normal_(0, 1)
        # forget-bias = 1.0
        # https://discuss.pytorch.org/t/set-forget-gate-bias-of-lstm/1745
        for l in six.moves.range(len(self.dec.decoder)):
            set_forget_bias_to_one(self.dec.decoder[l].bias_ih)

    def forward(self, xs_pad, ilens, ys_pad, tts_model=None, spembs=None, asrtts=False, ttsasr=False, rnnlm=None):
        """E2E forward

        :param torch.Tensor xs_pad: batch of padded input sequences (B, Tmax, idim)
        :param torch.Tensor ilens: batch of lengths of input sequences (B)
        :param torch.Tensor ys_pad: batch of padded character id sequence tensor (B, Lmax)
        :return: loass value
        :rtype: torch.Tensor
        """
        # 0. Frontend
        if self.frontend is not None:
            hs_pad, hlens, mask = self.frontend(to_torch_tensor(xs_pad), ilens)
            hs_pad, hlens = self.feature_transform(hs_pad, hlens)
        else:
            hs_pad, hlens = xs_pad, ilens

        # 1. Encoder
        if self.replace_sos:
            tgt_lang_ids = ys_pad[:, 0:1]
            ys_pad = ys_pad[:, 1:]  # remove target language ID in the beggining
        else:
            tgt_lang_ids = None
        
        if asrtts:
            noise = (0.1**0.5)*torch.randn(hs_pad.size()).cuda()
            hs_pad = hs_pad + noise
            hs_pad, hlens, _ = self.enc(hs_pad, hlens)
        else:
            hs_pad, hlens, _ = self.enc(hs_pad, hlens)
        # 2. CTC loss
        if self.mtlalpha == 0:
            self.loss_ctc = None
        else:
            self.loss_ctc = self.ctc(hs_pad, hlens, ys_pad)
        if asrtts:
            acc = None
            self.acc = acc
            # 4. compute cer without beam search
            cer_ctc = None
            if self.recog_args.ctc_weight > 0.0:
                lpz = self.ctc.log_softmax(hs_pad).data
            else:
                lpz = None
            #if self.recog_args.sampling == 'multinomial':
                #self.loss_att, best_hyps = self.dec.generate(hs_pad, torch.tensor(hlens), ys_pad, self.recog_args)
            fake_loss_att, lm_loss, best_hyps = self.dec.generate_forward(hs_pad, torch.tensor(hlens), ys_pad, self.recog_args, rnnlm=rnnlm, oracle_length=self.oracle_length)
            #else:
            #    best_hyps, pred_scores = self.dec.generate_beam_batch(
            #        hs_pad, torch.tensor(hlens), lpz,
            #        self.recog_args, self.char_list,
            #        self.rnnlm, tgt_lang_ids=None, sampling=self.recog_args.sampling)
            #    self.loss_att = pred_scores.mean(2)[:, -self.recog_args.nbest:].view(-1)
            #self.loss_att *= (np.mean([len(x) for x in best_hyps]) - 1)
            #self.loss_att = fake_loss_att
            if tts_model:
                self.loss_att, tts_loss = self.asr_to_tts(tts_model, fake_loss_att, lm_loss, best_hyps, ilens, xs_pad, spembs)
            else:
                self.loss_att = fake_loss_att
                tts_loss = torch.zeros(self.loss_att.size())
            logging.info(self.loss_att.mean())
        elif ttsasr:
             # 3. attention loss
            if self.mtlalpha == 1:
                self.loss_att, acc = None, None
            else:
                ys_pad[ys_pad==-1] = 0
                #_, _, _ = tts_model.unsup_inference(ys_pad, xs_pad, spembs=spembs) 
                #hs_zero_pad = torch.zeros(hs_pad.size()).cuda()
                self.loss_att, acc, _ = self.dec(hs_pad, hlens, ys_pad, tgt_lang_ids=tgt_lang_ids, ttsasr=True)
                #self.loss_att = torch.zeros(hs_pad.size(0))
                # masking acc 
                # logging.info("TTS2ASR, ACC: " + str(acc))
                #acc = None
            self.acc = acc
            # 4. compute cer without beam search
            if self.mtlalpha == 0:
                cer_ctc = None
            else:
                cers = []

                y_hats = self.ctc.argmax(hs_pad).data
                for i, y in enumerate(y_hats):
                    y_hat = [x[0] for x in groupby(y)]
                    y_true = ys_pad[i]

                    seq_hat = [self.char_list[int(idx)] for idx in y_hat if int(idx) != -1]
                    seq_true = [self.char_list[int(idx)] for idx in y_true if int(idx) != -1]
                    seq_hat_text = "".join(seq_hat).replace(self.space, ' ')
                    seq_hat_text = seq_hat_text.replace(self.blank, '')
                    seq_true_text = "".join(seq_true).replace(self.space, ' ')

                    hyp_chars = seq_hat_text.replace(' ', '')
                    ref_chars = seq_true_text.replace(' ', '')
                    if len(ref_chars) > 0:
                        cers.append(editdistance.eval(hyp_chars, ref_chars) / len(ref_chars))

                cer_ctc = sum(cers) / len(cers) if cers else None
        else:
            # 3. attention loss
            if self.mtlalpha == 1:
                self.loss_att, acc = None, None
            else:
                self.loss_att, acc, _ = self.dec(hs_pad, hlens, ys_pad, tgt_lang_ids=tgt_lang_ids)
            self.acc = acc
            # 4. compute cer without beam search
            if self.mtlalpha == 0:
                cer_ctc = None
            else:
                cers = []

                y_hats = self.ctc.argmax(hs_pad).data
                for i, y in enumerate(y_hats):
                    y_hat = [x[0] for x in groupby(y)]
                    y_true = ys_pad[i]

                    seq_hat = [self.char_list[int(idx)] for idx in y_hat if int(idx) != -1]
                    seq_true = [self.char_list[int(idx)] for idx in y_true if int(idx) != -1]
                    seq_hat_text = "".join(seq_hat).replace(self.space, ' ')
                    seq_hat_text = seq_hat_text.replace(self.blank, '')
                    seq_true_text = "".join(seq_true).replace(self.space, ' ')

                    hyp_chars = seq_hat_text.replace(' ', '')
                    ref_chars = seq_true_text.replace(' ', '')
                    if len(ref_chars) > 0:
                        cers.append(editdistance.eval(hyp_chars, ref_chars) / len(ref_chars))

                cer_ctc = sum(cers) / len(cers) if cers else None

        # 5. compute cer/wer
        if self.training or not (self.report_cer or self.report_wer):
            cer, wer = 0.0, 0.0
            # oracle_cer, oracle_wer = 0.0, 0.0
        else:
            if self.recog_args.ctc_weight > 0.0:
                lpz = self.ctc.log_softmax(hs_pad).data
            else:
                lpz = None
            word_eds, word_ref_lens, char_eds, char_ref_lens = [], [], [], []
            nbest_hyps = self.dec.recognize_beam_batch(
                hs_pad, torch.tensor(hlens), lpz,
                self.recog_args, self.char_list,
                self.rnnlm,
                tgt_lang_ids=tgt_lang_ids.squeeze(1).tolist() if self.replace_sos else None)
            # remove <sos> and <eos>
            y_hats = [nbest_hyp[0]['yseq'][1:-1] for nbest_hyp in nbest_hyps]
            for i, y_hat in enumerate(y_hats):
                y_true = ys_pad[i]

                seq_hat = [self.char_list[int(idx)] for idx in y_hat if int(idx) != -1]
                seq_true = [self.char_list[int(idx)] for idx in y_true if int(idx) != -1]
                seq_hat_text = "".join(seq_hat).replace(self.recog_args.space, ' ')
                seq_hat_text = seq_hat_text.replace(self.recog_args.blank, '')
                seq_true_text = "".join(seq_true).replace(self.recog_args.space, ' ')

                hyp_words = seq_hat_text.split()
                ref_words = seq_true_text.split()
                word_eds.append(editdistance.eval(hyp_words, ref_words))
                word_ref_lens.append(len(ref_words))
                hyp_chars = seq_hat_text.replace(' ', '')
                ref_chars = seq_true_text.replace(' ', '')
                char_eds.append(editdistance.eval(hyp_chars, ref_chars))
                char_ref_lens.append(len(ref_chars))

            wer = 0.0 if not self.report_wer else float(sum(word_eds)) / sum(word_ref_lens)
            cer = 0.0 if not self.report_cer else float(sum(char_eds)) / sum(char_ref_lens)
        alpha = self.mtlalpha
        if alpha == 0:
            self.loss = self.loss_att
            loss_att_data = float(self.loss_att.mean())
            loss_ctc_data = None
        elif alpha == 1:
            self.loss = self.loss_ctc
            loss_att_data = None
            loss_ctc_data = float(self.loss_ctc)
        else:
            if asrtts:
                self.loss = self.loss_att
            else:
                self.loss = alpha * self.loss_ctc + (1 - alpha) * self.loss_att.mean()
            loss_att_data = float(self.loss_att.mean())
            loss_ctc_data = float(self.loss_ctc)
        
        loss_data = float(self.loss.mean())
        #logging.info("main acc is: " + str(acc))
        if asrtts:
            self.reporter.report(loss_ctc_data, loss_att_data, acc, cer_ctc, cer, wer, loss_data, float(tts_loss.mean()))
            return self.loss
        elif ttsasr:
            if loss_data < CTC_LOSS_THRESHOLD and not math.isnan(loss_data):
                self.reporter.report(loss_ctc_data, loss_att_data, acc, cer_ctc, cer, wer, loss_data, None)
            else:
                logging.warning('loss (=%f) is not correct', loss_data)
            return self.loss
        else:
            if loss_data < CTC_LOSS_THRESHOLD and not math.isnan(loss_data):
                self.reporter.report(loss_ctc_data, loss_att_data, acc, cer_ctc, cer, wer, loss_data, None)
            else:
                logging.warning('loss (=%f) is not correct', loss_data)
            return self.loss

    def random_sampler(self, hyps, xlens, xs, spembs):
        # convert hyps to xs, xlens to ylens, ys to xs
        # separate yseq from dictionary of nbest_hyps
        hyps = torch.cat(hyps).long()
        xs_tts = to_device(self, hyps)
        xlens_tts = to_device(self, torch.from_numpy(np.array([y.shape[0] for y in hyps])))
        xlens_tts = sorted(xlens_tts, reverse=True)
        #xs_tts = to_device(self, pad_list([y.long() for y in ys], 0)).view(xs.shape(0), -1)
        from espnet.utils.training.asrtts_train_utils import mask_by_length_and_multiply
        xs, xlens = mask_by_length_and_multiply(xs, xlens, 0, self.recog_args.nbest)
        onelens = np.fromiter((1 for xx in spembs), dtype=np.int64)
        spembs, _ = mask_by_length_and_multiply(spembs.unsqueeze(1), torch.tensor(onelens), 0, self.recog_args.nbest)
        spembs = spembs.squeeze(1)
        ylens_tts = torch.Tensor([ torch.max(xlens) for _ in range(len(xlens)) ]).type(xlens.dtype)
        ys_tts = xs
        labels = ys_tts.new_zeros(ys_tts.size(0), ys_tts.size(1))
        for i, l in enumerate(ylens_tts):
            labels[i, l - 1:] = 1.0

        return xs_tts, xlens_tts, ys_tts, labels, ylens_tts, spembs

    def policy_rewards(self, log_probs, rewards):
        gamma = 0.99
        R = 0
        eps = np.finfo(np.float32).eps.item()
        returns = rewards
        policy_loss = []
        #for n, r in enumerate(rewards):
        #    R = r + gamma * R
        #    returns[n] = R
        returns = torch.tensor(returns)
        returns = (returns - returns.mean()) #/ (returns.std() + eps)
        for log_prob, R in zip(log_probs, returns):
            policy_loss.append(log_prob * R)
        policy_loss = torch.stack(policy_loss)
        return policy_loss

    def asr_to_tts(self, tts_model, fake_loss, lm_loss, best_hyps, ilens, xs_pad, spembs):
        x_tts = self.random_sampler(best_hyps, ilens, xs_pad, spembs)
        tts_loss, after_outs, before_outs, logits, att_ws = tts_model(*x_tts+(True,))
        tts_loss = tts_loss + lm_loss
        policy_loss = self.policy_rewards(fake_loss, tts_loss)

        return policy_loss, tts_loss

    def scorers(self):
        return dict(decoder=self.dec, ctc=CTCPrefixScorer(self.ctc, self.eos))

    def encode(self, x):
        self.eval()
        ilens = [x.shape[0]]

        # subsample frame
        x = x[::self.subsample[0], :]
        p = next(self.parameters())
        h = torch.as_tensor(x, device=p.device, dtype=p.dtype)
        # make a utt list (1) to use the same interface for encoder
        hs = h.contiguous().unsqueeze(0)

        # 0. Frontend
        if self.frontend is not None:
            enhanced, hlens, mask = self.frontend(hs, ilens)
            hs, hlens = self.feature_transform(enhanced, hlens)
        else:
            hs, hlens = hs, ilens

        # 1. encoder
        hs, _, _ = self.enc(hs, hlens)
        return hs.squeeze(0)

    def recognize(self, x, recog_args, char_list, rnnlm=None):
        """E2E beam search

        :param ndarray x: input acoustic feature (T, D)
        :param Namespace recog_args: argument Namespace containing options
        :param list char_list: list of characters
        :param torch.nn.Module rnnlm: language model module
        :return: N-best decoding results
        :rtype: list
        """
        hs = self.encode(x).unsqueeze(0)
        # calculate log P(z_t|X) for CTC scores
        if recog_args.ctc_weight > 0.0:
            lpz = self.ctc.log_softmax(hs)[0]
        else:
            lpz = None

        # 2. Decoder
        # decode the first utterance
        y = self.dec.recognize_beam(hs[0], lpz, recog_args, char_list, rnnlm)
        return y

    def recognize_batch(self, xs, recog_args, char_list, rnnlm=None):
        """E2E beam search

        :param list xs: list of input acoustic feature arrays [(T_1, D), (T_2, D), ...]
        :param Namespace recog_args: argument Namespace containing options
        :param list char_list: list of characters
        :param torch.nn.Module rnnlm: language model module
        :return: N-best decoding results
        :rtype: list
        """
        prev = self.training
        self.eval()
        ilens = np.fromiter((xx.shape[0] for xx in xs), dtype=np.int64)

        # subsample frame
        xs = [xx[::self.subsample[0], :] for xx in xs]
        xs = [to_device(self, to_torch_tensor(xx).float()) for xx in xs]
        xs_pad = pad_list(xs, 0.0)

        # 0. Frontend
        if self.frontend is not None:
            enhanced, hlens, mask = self.frontend(xs_pad, ilens)
            hs_pad, hlens = self.feature_transform(enhanced, hlens)
        else:
            hs_pad, hlens = xs_pad, ilens

        # 1. Encoder
        hs_pad, hlens, _ = self.enc(hs_pad, hlens)

        # calculate log P(z_t|X) for CTC scores
        if recog_args.ctc_weight > 0.0:
            lpz = self.ctc.log_softmax(hs_pad)
            normalize_score = False
        else:
            lpz = None
            normalize_score = True

        # 2. Decoder
        hlens = torch.tensor(list(map(int, hlens)))  # make sure hlens is tensor
        y = self.dec.recognize_beam_batch(hs_pad, hlens, lpz, recog_args, char_list,
                                          rnnlm, normalize_score=normalize_score)

        if prev:
            self.train()
        return y

    def enhance(self, xs):
        """Forwarding only the frontend stage

        :param ndarray xs: input acoustic feature (T, C, F)
        """

        if self.frontend is None:
            raise RuntimeError('Frontend does\'t exist')
        prev = self.training
        self.eval()
        ilens = np.fromiter((xx.shape[0] for xx in xs), dtype=np.int64)

        # subsample frame
        xs = [xx[::self.subsample[0], :] for xx in xs]
        xs = [to_device(self, to_torch_tensor(xx).float()) for xx in xs]
        xs_pad = pad_list(xs, 0.0)
        enhanced, hlensm, mask = self.frontend(xs_pad, ilens)
        if prev:
            self.train()
        return enhanced.cpu().numpy(), mask.cpu().numpy(), ilens

    def calculate_all_attentions(self, xs_pad, ilens, ys_pad):
        """E2E attention calculation

        :param torch.Tensor xs_pad: batch of padded input sequences (B, Tmax, idim)
        :param torch.Tensor ilens: batch of lengths of input sequences (B)
        :param torch.Tensor ys_pad: batch of padded character id sequence tensor (B, Lmax)
        :return: attention weights with the following shape,
            1) multi-head case => attention weights (B, H, Lmax, Tmax),
            2) other case => attention weights (B, Lmax, Tmax).
        :rtype: float ndarray
        """
        with torch.no_grad():
            # 0. Frontend
            if self.frontend is not None:
                hs_pad, hlens, mask = self.frontend(to_torch_tensor(xs_pad), ilens)
                hs_pad, hlens = self.feature_transform(hs_pad, hlens)
            else:
                hs_pad, hlens = xs_pad, ilens

            # 1. Encoder
            if self.replace_sos:
                tgt_lang_ids = ys_pad[:, 0:1]
                ys_pad = ys_pad[:, 1:]  # remove target language ID in the beggining
            else:
                tgt_lang_ids = None
            hpad, hlens, _ = self.enc(hs_pad, hlens)

            # 2. Decoder
            att_ws = self.dec.calculate_all_attentions(hpad, hlens, ys_pad, tgt_lang_ids=tgt_lang_ids)

        return att_ws

    def subsample_frames(self, x):
        # subsample frame
        x = x[::self.subsample[0], :]
        ilen = [x.shape[0]]
        h = to_device(self, torch.from_numpy(
            np.array(x, dtype=np.float32)))
        h.contiguous()
        return h, ilen
