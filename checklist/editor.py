import collections
import itertools
import string
import numpy as np
import re
import copy
import os
import json
import munch

def recursive_format(obj, mapping):
    def formatfn(x, mapping):
        options = re.compile(r'{([^}]+):([^}]+)}')
        def mysub(match):
            options, thing = match.group(1, 2)
            ret = ''
            if 'a' in options:
                word = ('{%s}' % thing).format(**mapping)
                ret += '%s ' % add_article(word).split()[0]
            ret += '{%s}' % thing
            return ret
        # print(x)
        x = options.sub(mysub, x)
        # print(x)
        return x.format(**mapping)
    return recursive_apply(obj, formatfn, mapping)

def recursive_apply(obj, fn, *args, **kwargs):
    if type(obj) in [str, bytes]:
        return fn(obj, *args, **kwargs)#obj.format(**(mapping))
    elif type(obj) == tuple:
        return tuple(recursive_apply(list(obj), fn, *args, **kwargs))
    elif type(obj) == list:
        return [recursive_apply(o, fn, *args, **kwargs) for o in obj]
    elif type(obj) == dict:
        return {k: recursive_apply(v, fn, *args, **kwargs) for k, v in obj.items()}
    else:
        return fn(obj, *args, **kwargs)
        # return obj

def replace_bert(obj):
    bert_finder = re.compile(r'\{((?:[^\}]*:)?bert\d*)\}')
    # bert_finder = re.compile(r'\{(bert\d*)\}')
    i = 0
    while bert_finder.search(obj):
        obj = bert_finder.sub(r'{\1[%d]}' % i, obj, 1)
        i += 1
    return obj

def add_article(noun):
    return 'an %s' % noun if noun[0].lower() in ['a', 'e', 'i', 'o', 'u'] else 'a %s' % noun

def find_all_keys(obj):
    strings = get_all_strings(obj)
    ret = set()
    for s in strings:
        f = string.Formatter()
        for x in f.parse(s):
            r = x[1] if not x[2] else '%s:%s' % (x[1], x[2])
            ret.add(r)
    return set([x for x in ret if x])

def get_bert_index(obj):
    strings = get_all_strings(obj)
    # ?: after parenthesis makes group non-capturing
    bert_finder = re.compile(r'\{(?:[^\}]*:)?bert\d*\}')
    bert_rep = re.compile(r'[\{\}]')
    remove_option = re.compile(r'.*:')
    ret = collections.defaultdict(lambda: [])
    for s in strings:
        berts = bert_finder.findall(s)
        nooptions = [bert_rep.sub('', remove_option.sub('', x)) for x in berts]
        if len(set(nooptions)) > 1:
            raise Exception('Can only have one bert index per template string')
        if nooptions:
            ret[nooptions[0]].append(s)
    return ret

def get_all_strings(obj):
    ret = set()
    if type(obj) in [str, bytes]:
        ret.add(obj)
    elif type(obj) in [tuple, list, dict]:
        if type(obj) == dict:
            obj = obj.values()
        k = [get_all_strings(x) for x in obj]
        k = [x for x in k if x]
        for x in k:
            ret = ret.union(x)
    return set([x for x in ret if x])

def wrapped_random_choice(x, *args, **kwargs):
    try:
        return np.random.choice(x, *args, **kwargs)
    except:
        idxs = np.random.choice(len(x), *args, **kwargs)
        return type(x)([x[i] for i in idxs])

class Editor(object):
    def __init__(self):
        cur_folder = os.path.dirname(__file__)
        folder = os.path.abspath(os.path.join(cur_folder, os.pardir, "data", 'lexicons'))
        self.lexicons = {}
        self.data = {}
        for f in os.listdir(folder):
            self.lexicons.update(json.load(open(os.path.join(folder, f))))
        make_munch = lambda x: munch.Munch(x) if type(x) == dict else x
        for x in self.lexicons:
            self.lexicons[x] = [make_munch(x) for x in self.lexicons[x]]
        self.data['names'] = json.load(open(os.path.join(cur_folder, os.pardir, 'data', 'names.json')))
        self.data['names'] = {x:set(self.data['names'][x]) for x in self.data['names']}


    def __getattr__(self, attr):
        if attr == 'tg':
            print
            from .text_generation import TextGenerator
            import spacy
            nlp = spacy.load('en_core_web_sm')
            self.tg = TextGenerator(nlp)
            return self.tg
        else:
            raise AttributeError
    # def init_tg(self):
    #     if self.tg is not None:
    #         return
    def suggest_replace(self, text, word, full_sentences=False, words_and_sentences=False, **kwargs):
        ret = self.tg.replace_word(text, word, **kwargs)
        if kwargs.get('verbose', False):
            print('\n'.join(['%6s %s' % ('%.2f' % x[2], x[1]) for x in ret[:5]]))
        if words_and_sentences:
            return [(tuple(x[0]), x[1]) if len(x[0]) > 1 else (x[0][0], x[1]) for x in ret]
        if full_sentences:
            return [x[1] for x in ret]
        else:
            return [tuple(x[0]) if len(x[0]) > 1 else x[0][0] for x in ret]

    def suggest(self, templates, **kwargs):
        # if replace:
        #     replace_with_bert = lambda x: re.sub(r'\b%s\b'% re.escape(replace), '{bert}', x)
        #     templates = recursive_apply(templates, replace_with_bert)
        bert_index = get_bert_index(templates)
        if not bert_index:
            return []
        if len(bert_index) != 1:
            raise Exception('Only one bert index is allowed')
        ret = self.template(templates, **kwargs, bert_only=True)
        xs = [tuple(x[0]) if len(x[0]) > 1 else x[0][0] for x in ret]
        if kwargs.get('verbose', False):
            print('\n'.join(['%6s %s' % ('%.2f' % x[2], x[1]) for x in ret[:5]]))
        return xs


    def add_lexicon(self, name, values, overwrite=False):
        # words can be strings, dictionarys, and other objects
        if name in self.lexicons and not overwrite:
            raise Exception('%s already in lexicons. Call with overwrite=True to overwrite' % name)
        self.lexicons[name] = values


    def template(self, templates, return_meta=False, nsamples=None, product=True, remove_duplicates=False, bert_only=False, unroll=False, **kwargs):
    # 1. go through object, find every attribute inside brackets
    # 2. check if they are in kwargs and self.attributes
    # 3. generate keys and vals
    # 4. go through object, generate
        templates = copy.deepcopy(templates)
        all_keys = find_all_keys(templates)
        items = {}
        bert_match = re.compile(r'bert\d*')
        for k in all_keys:
            # TODO: process if ends in number
            # TODO: process if is a:key to add article
            k = re.sub(r'\..*', '', k)
            k = re.sub(r'\[.*\]', '', k)
            k = re.sub(r'.*?:', '', k)
            newk = re.sub(r'\d+$', '', k)
            if bert_match.match(k):
                continue
            if newk in kwargs:
                items[k] = kwargs[newk]
            elif newk in self.lexicons:
                items[k] = self.lexicons[newk]
            else:
                raise(Exception('Error: key "%s" not in items or lexicons' % newk))
        bert_index = get_bert_index(templates)
        for bert, strings in bert_index.items():
            ks = {re.sub(r'.*?:', '', a): '{%s}' % a for a in all_keys}
            tok = 'VERYLONGTOKENTHATWILLNOTEXISTEVER'
            ks[bert] = tok

            a_tok = 'thisisaratherlongtokenthatwillnotexist'
            sub_a = lambda x: re.sub(r'{a:(%s)}' % bert, r'{%s} {\1}' % a_tok, x)
            strings = recursive_apply(strings, sub_a)
            ks[a_tok] = '{%s}' % a_tok
            ts = recursive_format(strings, ks)
            # print(ts)
            samp = self.template(ts, nsamples=20, remove_duplicates=remove_duplicates, # **kwargs)
                                 thisisaratherlongtokenthatwillnotexist=['a', 'an'], **kwargs)
            samp = [x.replace(tok, self.tg.bert_tokenizer.mask_token) for y in samp for x in y][:20]
            beam_size = kwargs.get('beam_size', 100)
            options = self.tg.unmask_multiple(samp, beam_size=beam_size, **kwargs)
            v = [x[0] for x in options][:10]
            items[bert] = v
            if bert_only:
                return options
        templates = recursive_apply(templates, replace_bert)
        # print(templates)
        keys = [x[0] for x in items.items()]
        vals = [[x[1]] if type(x[1]) not in [list, tuple] else x[1] for x in items.items()]
        if nsamples is not None:
            # v = [np.random.choice(x, nsamples) for x in vals]
            v = [wrapped_random_choice(x, nsamples) for x in vals]
            if not v:
                vals = [[]]
            else:
                vals = zip(*v)
            # print(list(vals))
        else:
            if not product:
                vals = zip(*vals)
            else:
                vals = itertools.product(*vals)
        ret = []
        ret_maps = []
        for v in vals:
            # print(v)
            if remove_duplicates and len(v) != len(set([str(x) for x in v])):
                continue
            mapping = dict(zip(keys, v))
            # print(templates)
            # print(mapping)
            ret.append(recursive_format(templates, mapping))
            ret_maps.append(mapping)
        if unroll and ret and type(ret[0]) in [list, np.array, tuple]:
            ret = [x for y in ret for x in y]
            ret_maps = [x for y in ret_maps for x in y]
        if return_meta:
            return ret, ret_maps
        return ret
