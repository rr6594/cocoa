# -*- coding: utf-8 -*-
import random
import re
from collections import defaultdict
from src.basic.sample_utils import sample_candidates
from session import Session
from src.model.preprocess import tokenize, word_to_num
from src.model.vocab import is_entity
from src.basic.lexicon import Lexicon
import numpy as np
from itertools import izip
from google.cloud import translate

# Instantiates a client
translate_client = translate.Client()

num_to_word = {v: k for k, v in word_to_num.iteritems()}

class SimpleSession(Session):
    '''
    The simple system implements a bot that
    - greets
    - asks or informs about a fact in its KB
    - replies 'no' or 'yes' to partner's response
    - selects and item
    '''

    # greetings = ['hi', 'hello', 'hey', 'hiya']
    # TODO: read in as list, in case we change it up more later
    greetings = ['hola', 'que pasa']  # 'oye'

    def __init__(self, agent, kb, lexicon, realizer=None, consecutive_entity=True):
        super(SimpleSession, self).__init__(agent)
        self.agent = agent
        self.kb = kb
        self.attr_type = {attr.name: attr.value_type for attr in kb.attributes}
        self.lexicon = lexicon
        self.realizer = realizer
        self.consecutive_entity = consecutive_entity
        self.num_items = len(kb.items)
        self.entity_coords = self.get_entity_coords()
        # print "ENTITY_COORDS", self.entity_coords
        # print '*'*20
        # print type(kb.items)
        # print len(kb.items)
        # print kb.items
        # print '*'*20

        self.entity_weights = self.weight_entity()
        self.item_weights = [1.] * self.num_items

        self.sent_entity = False
        self.mentioned_entities = set()

        # Dialogue state
        self.asked_entities = None
        self.answered = False
        self.said_hi = False
        self.matched_item = None
        self.selected = False

        self.capitalize = random.choice([True, False])
        self.numerical = random.choice([True, False])

        # new
        self.biling_dct = self.get_biling_dict()
        self.phrase_table = self.get_phrase_table()
        # print self.biling_dct.keys()

    # new methods
    # 1)
    # NOTE: no distinction of entity categories, only simple dictionary look-up
    @staticmethod
    def get_biling_dict():
        # assume each line in file has tab-separated fields: EN, SP
        content_en = {}
        content_sp = {}

        def add_to_biling_dict(fname):
            with open(fname, 'r') as fin:
                for line in fin.readlines():
                    eng, spa = line.replace('\n','').decode('utf-8').split('\t')
                    content_en[eng] = spa
                    content_sp[spa] = eng

        # add_to_biling_dict('data/names.txt')
        add_to_biling_dict('data/hobbies.txt')
        add_to_biling_dict('data/loc.txt')
        add_to_biling_dict('data/time.txt')
        add_to_biling_dict('data/majors.txt')
        content_en['friend'] = 'amigo'
        content_en['friends'] = 'amigos'
        content_sp['amigo'] = 'friend'
        content_sp['amigos'] = 'friends'


        return dict([('en', content_en), ('sp', content_sp)])

    # 2)
    ''' returns:    phrase_table['en'][eng_word] = list({spa_word: attrib})
                    phrase_table['sp'][spa_word]['eng'] = eng_word
                    phrase_table['sp'][spa_word]['attrib'] = attrib
    '''
    def get_phrase_table(self):
        # assume each line in file has fields: EN, SP, number, gender, formal, tense
        content_en = defaultdict(list)
        content_sp = defaultdict(dict)

        with open('data/fillers.txt', 'r') as fin:
            for line in fin.readlines():
                eng, spa, num, gender, formal, tense = line.replace('\n','').decode('utf-8').split('\t')
                attrib = ','.join([num, gender, formal])
                span_attrib = defaultdict(dict)
                span_attrib[spa]['attrib'] = attrib
                content_en[eng].append(span_attrib)

                content_sp[spa]['eng'] = eng
                content_sp[spa]['attrib'] = attrib

        return dict([('en', content_en), ('sp', content_sp)])

    # 3)
    def stylize(self, orig_eng, style_type='en_lex'):
        # en_tokens = orig_eng.split()

        # STYLE 1: EN as matrix, replace SP nouns
        def en_lex():
            new_str = orig_eng
            for en_chunk, sp_chunk in self.biling_dct['en'].iteritems():
                # if en_chunk in new_str:
                new_str = new_str.replace(en_chunk, sp_chunk)
            return new_str

            # eng_matrix = []
            # for token in en_tokens:
            #     if token in self.biling_dct['en'].keys():
            #         token = self.biling_dct['en'][token]
            #         print 'NEW TOKEN', token
            #     eng_matrix.append(token)
            # new_str = ' '.join(eng_matrix)

        # STYLE 2: SP as matrix, replace EN nouns
        def sp_lex():
            translation = translate_client.translate(orig_eng, target_language='es')
            orig_spa = translation['translatedText'].replace('.', ' .').replace('?', ' ?')

            new_str = orig_spa
            for sp_chunk, en_chunk in self.biling_dct['sp'].iteritems():
                # if sp_chunk in new_str:
                new_str = new_str.replace(sp_chunk, en_chunk)
            return new_str

        if style_type == 'en_lex':
            new_str = en_lex()
        elif style_type == 'sp_lex':
            new_str = sp_lex()

        # new_str = new_str.replace(' .','.').replace(' ?','?')
        print '*'*20
        print 'NEW STR', new_str
        print '*'*20
        return new_str.encode('utf-8')

    def get_entity_coords(self):
        '''
        Return a dict of {entity: [row]}
        '''
        entity_coords = defaultdict(list)
        for row, item in enumerate(self.kb.items):
            for col, attr in enumerate(self.kb.attributes):
                entity = (item[attr.name].lower(), attr.value_type)
                entity_coords[entity].append(row)
        return entity_coords

    def get_related_entity(self, entities):
        '''
        Return entities in the same row and col as the input entities.
        '''
        rows = set()
        types = set()
        for entity in entities:
            rows.update(self.entity_coords[entity])
            types.add(entity[1])
        cols = []
        for i, attr in enumerate(self.kb.attributes):
            if attr.value_type in types:
                cols.append(i)
        row_entities = set()
        col_entities = set()
        for row, item in enumerate(self.kb.items):
            for col, attr in enumerate(self.kb.attributes):
                entity = (item[attr.name].lower(), attr.value_type)
                if entity in entities:
                    continue
                if row in rows:
                    row_entities.add(entity)
                if col in cols:
                    col_entities.add(entity)
        return row_entities, col_entities

    def count_entity(self):
        '''
        Return a dict of {entity: count}.
        '''
        entity_counts = defaultdict(int)
        for item in self.kb.items:
            for attr_name, entity in item.iteritems():
                entity = (entity.lower(), self.attr_type[attr_name])
                entity_counts[entity] += 1
        return entity_counts

    def weight_entity(self):
        '''
        Assign weights to each entity.
        '''
        entity_counts = self.count_entity()
        N = float(self.num_items)
        # Scale counts to [0, 1]
        entity_weights = {entity: count / N for entity, count in entity_counts.iteritems()}
        return entity_weights

    def choose_fact(self):
        num_entities = np.random.randint(1, 3)
        entities = sample_candidates(self.entity_weights.items(), num_entities)
        return self.entity_to_fact(entities), entities

    def entity_to_fact(self, entities):
        facts = []
        while len(entities) > 0:
            i = 0
            entity = entities[i]
            rowi = self.entity_coords[entity]
            fact = [[entity], len(rowi)]
            for j in xrange(i+1, len(entities)):
                entity2 = entities[j]
                rowj = self.entity_coords[entity2]
                intersect = [r for r in rowi if r in rowj]
                if len(intersect) > 0:
                    fact[0].append(entity2)
                    fact[1] = len(intersect)
                    rowi = intersect
            # Remove converted entities
            entities = [entity for entity in entities if entity not in fact[0]]
            facts.append(fact)
        return facts

    def fact_to_str(self, fact, num_items, include_count=True, prefix=False, question=False):
        fact_str = []
        total = num_items
        for entities, count in fact:
            # Add caninical form as surface form
            entities = [(x[0], x) for x in entities]
            if self.realizer:
                entities_str = self.realizer.realize_entity(entities)
            else:
                entities_str = [x[0] for x in entities]
            if prefix:
                new_str = []
                for entity, s in izip(entities, entities_str):
                    entity_type = entity[1][1]
                    if entity_type == 'name':
                        p = 'named'
                    elif entity_type == 'school':
                        p = 'who went to'
                    elif entity_type == 'company':
                        p = 'working at'
                    elif entity_type == 'major':
                        p = 'who studied'
                    else:
                        # modified singular surface form of like --> 'likes'
                        # remove option 'into'
                        # p = random.choice(['who like' if count > 1 else 'who likes', 'into'])
                        p = random.choice(['who like' if count > 1 else 'who likes'])
                    new_str.append(p + ' ' + s)
                entities_str = new_str
            conj = '%s' % ('friends' if count > 1 else 'friend') if prefix else ''
            if include_count:
                s = '%s %s %s' % (self.number_to_str(count, total), conj, ' and '.join([a for a in entities_str]))
            else:
                s = '%s %s' % (conj, ' and '.join([a for a in entities_str]))
            fact_str.append(s)
        if question and len(fact_str) > 1:
            fact_str = ', '.join(fact_str[:-1]) + ' or ' + fact_str[-1]
        else:
            fact_str = ', '.join(fact_str)
        fact_str = fact_str.replace('  ', ' ')
        return fact_str

    def number_to_str(self, count, total):
        if count == 0:
            return 'no'
        elif count == 1:
            return '1'
        elif count == total:
            return 'all'
        elif count == 2:
            return '2'
        elif count > 2./3. * total:
            return 'many'
        else:
            return 'some'

    def naturalize(self, text):
        tokens = text.split()
        if self.capitalize:
            tokens[0] = tokens[0].title()
            tokens = ['I' if x == 'i' else x for x in tokens]
        if not self.numerical:
            tokens = [num_to_word[x] if x in num_to_word else x for x in tokens]
        return ' '.join(tokens)

    def inform(self, facts):
        # fact_str = self.fact_to_str(facts, self.num_items, prefix=random.choice([False, True]))
        # make prefix True to give longer, grammatical sentences
        fact_str = self.fact_to_str(facts, self.num_items, prefix=True)
        # message = self.naturalize('i have %s .' % fact_str)
        message = self.naturalize('i have %s' % fact_str) # remove period @ sent end
        print '*'*20, 'inform'
        print 'BEFORE', message
        message = self.stylize(message) # ADDED
        print 'AFTER', message
        return self.message(message)

    def ask(self, facts):
        # fact_str = self.fact_to_str(facts, self.num_items, include_count=False, prefix=random.choice([False, True]), question=True)
        fact_str = self.fact_to_str(facts, self.num_items, include_count=False, prefix=True, question=True)
        message = self.naturalize('do you have any %s ?' % fact_str)
        print '*'*20, 'ask'
        print 'BEFORE', message
        message = self.stylize(message) # ADDED
        print 'AFTER', message
        return self.message(message)

    def answer(self, entities): #TODO: HANDLE RECEIVING
        fact = self.entity_to_fact(entities)
        return self.inform(fact)

    def sample_item(self):
        item_id = np.argmax(self.item_weights)
        return item_id

    def update_entity_weights(self, entities, delta):
        for entity in entities:
            if entity in self.entity_weights:
                self.entity_weights[entity] += delta

    def update_item_weights(self, entities, delta):
        for i, item in enumerate(self.kb.items):
            values = [v.lower() for v in item.values()]
            self.item_weights[i] += delta * len([entity for entity in entities if entity[0] in values])

    def send(self):
        # Don't send consecutive utterances with entities
        if self.sent_entity and not self.consecutive_entity:
            return None
        if self.matched_item:
            if not self.selected:
                self.selected = True
                return self.select(self.matched_item)
            else:
                return None

        # Say hi first
        if not self.said_hi:
            self.said_hi = True
            text = random.choice(self.greetings)
            return self.message(text)

        # Reply to questions
        if self.asked_entities is not None:
            response = self.answer(self.asked_entities)
            self.asked_entities = None
            return response

        # Inform or Ask or Select
        if (not self.can_select()) or np.random.random() < 0.7:
            self.sent_entity = True
            facts, entities = self.choose_fact()
            # Decrease weights of entities mentioned
            self.update_entity_weights(entities, -10.)
            if np.random.random() < 0.5:
                return self.inform(facts)
            else:
                return self.ask(facts)
        else:
            item_id = self.sample_item()
            # Don't repeatedly select one item
            self.item_weights[item_id] = -100
            return self.select(self.kb.items[item_id])

    def can_select(self):
        '''
        We can select only when at least on item has weight > 1.
        '''
        if max(self.item_weights) > 1.:
            return True
        return False

    def is_question(self, tokens): #TODO: HANDLE RECEIVING
        first_word = tokens[0]
        last_word = tokens[-1]
        if last_word == '?' or first_word in ('do', 'does', 'what', 'any'):
            return True
        return False

    def receive(self, event): #TODO: HANDLE RECEIVING
        self.sent_entity = False
        if event.action == 'message':
            raw_utterance = event.data

            # handle bilingual. translate into English no matter what source lang is
            translation = translate_client.translate(raw_utterance, target_language='en')
            new_eng = translation['translatedText']
            # entity_tokens = self.lexicon.link_entity(tokenize(raw_utterance), kb=self.kb, mentioned_entities=self.mentioned_entities, known_kb=False)
            entity_tokens = self.lexicon.link_entity(tokenize(new_eng), kb=self.kb, mentioned_entities=self.mentioned_entities, known_kb=False)
            for token in entity_tokens:
                if is_entity(token):
                    # it works!!
                    # print '***'
                    # print 'IS ENTITY:', token
                    # print '***'
                    self.mentioned_entities.add(token[1][0])
            entities = [word[1] for word in entity_tokens if is_entity(word)]

            if self.is_question(entity_tokens):
                self.asked_entities = entities

            # Update item weights
            if len(entities) > 0:
                if len([x for x in entity_tokens if x in ('no', 'none', "don't", 'zero')]) > 0:
                    negative = True
                else:
                    negative = False
                self.update_item_weights(entities, -10. if negative else 1.)

                row_entities, col_entities = self.get_related_entity(entities)
                self.update_entity_weights(entities, -10. if negative else 1.)
                self.update_entity_weights(row_entities, -1. if negative else 2.)
                self.update_entity_weights(col_entities, 1. if negative else 0.5)

        elif event.action == 'select':
            for item in self.kb.items:
                if item == event.data:
                    self.matched_item = item
