"""
intro2nlp, assignment 4, 2020

In this assignment you will implement a Hidden Markov model and an LSTM model
to predict the part of speech sequence for a given sentence.
(Adapted from Nathan Schneider)

"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.autograd as autograd
from torch.autograd import Variable
from torchtext import data
from torchtext import datasets
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
import torchtext.vocab as torch_vocab

from math import log, isfinite, inf
from collections import Counter
import numpy as np
import sys, os, time, platform, nltk, random
import pickle 
import pandas as pd

from tqdm import tqdm

# With this line you don't need to worry about the HW  -- GPU or CPU
# GPU cuda cores will be used if available
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# You can call use_seed with other seeds or None (for complete randomization)
# but DO NOT change the default value.
def use_seed(seed = 1512021):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_deterministic(True)
    #torch.backends.cudnn.deterministic = True

# utility functions to read the corpus
def who_am_i(): #this is not a class method
    """Returns a ductionary with your name, id number and email. keys=['name', 'id','email']
        Make sure you return your own info!
    """
    # if work is submitted by a pair of students, add the following keys: name2, id2, email2
    return {'name1': 'or oxenberg', 'id1': '312460132', 'email1': 'orox@post.bgu.ac.il',
            'name2': 'sahar baribi', 'id2': '311232730', 'email2': 'saharba@post.bgu.ac.il'}


def read_annotated_sentence(f):
    line = f.readline()
    if not line:
        return None
    sentence = []
    while line and (line != "\n"):
        line = line.strip()
        word, tag = line.split("\t", 2)
        sentence.append( (word, tag) )
        line = f.readline()
    return sentence

def load_annotated_corpus(filename):
    sentences = []
    with open(filename, 'r', encoding='utf-8') as f:
        sentence = read_annotated_sentence(f)
        while sentence:
            sentences.append(sentence)
            sentence = read_annotated_sentence(f)
    return sentences



START = "<DUMMY_START_TAG>"
END = "<DUMMY_END_TAG>"
UNK = "<UNKNOWN>"
  
allTagCounts = Counter()
# use Counters inside these
perWordTagCounts = {}
transitionCounts = {}
emissionCounts = {}
# log probability distributions: do NOT use Counters inside these because
# missing Counter entries default to 0, not log(0)
A = {} #transisions probabilities
B = {} #emmissions probabilities

def learn_params(tagged_sentences):
    
    """Populates and returns the allTagCounts, perWordTagCounts, transitionCounts,
      and emissionCounts data-structures.
    allTagCounts and perWordTagCounts should be used for baseline tagging and
    should not include pseudocounts, dummy tags and unknowns.
    The transisionCounts and emmisionCounts
    should be computed with pseudo tags and shoud be smoothed.
    A and B should be the log-probability of the normalized counts, based on
    transisionCounts and  emmisionCounts

    Args:
      tagged_sentences: a list of tagged sentences, each tagged sentence is a
        list of pairs (w,t), as retunred by load_annotated_corpus().

    Return:
      [allTagCounts,perWordTagCounts,transitionCounts,emissionCounts,A,B] (a list)
    """
    
    transitionCounts[START] = Counter()
    all_words_count = 0
    
    for sentence in tagged_sentences:
        previous_tag = START
        for word,tag in sentence:
            all_words_count +=1
            #: count tags
            allTagCounts[tag] +=1
            
            #: check if the not word in perWordTagCounts dict
            if word not in perWordTagCounts:
                perWordTagCounts[word] = Counter()
            if tag not in emissionCounts:
                emissionCounts[tag] = Counter()
                transitionCounts[tag] = Counter()
            #: count tags
            perWordTagCounts[word][tag] +=1
            transitionCounts[previous_tag][tag]+=1
            emissionCounts[tag][word] +=1 
            previous_tag = tag
            
        transitionCounts[previous_tag][END]+=1
        
    A = transitionCounts.copy()
    B = emissionCounts.copy()
    
    
    #: filling A
    for tag, dictOfTags in A.items():
        newDictOfTags = {}
        for keyTag in dictOfTags: 
            if tag == START or tag ==END:
                newDictOfTags[keyTag] = log(dictOfTags[keyTag]/len(tagged_sentences))
            elif allTagCounts[tag]==0:
                newDictOfTags[keyTag] = 0
            else:
                newDictOfTags[keyTag] = log(dictOfTags[keyTag]/allTagCounts[tag])
        A[tag] = newDictOfTags
    
    #: filling B
    for tag, dictOfWords in B.items():
        dictOfWords = {k:log(v/allTagCounts[tag]) for k,v in dictOfWords.items()}
        B[tag] = dictOfWords
        B[tag][UNK] = allTagCounts[tag]/all_words_count
    
    #: Handling padding values
    emissionCounts[START] = Counter({START:len(tagged_sentences)})
    emissionCounts[END] = Counter({END:len(tagged_sentences)})   

    B[UNK] = {}
    B[UNK]['<UNKNOWN>'] = 0
    
    return [allTagCounts,perWordTagCounts,transitionCounts,emissionCounts,A,B]

def baseline_tag_sentence(sentence, perWordTagCounts, allTagCounts):
    """Returns a list of pairs (w,t) where each w corresponds to a word
    (same index) in the input sentence. Each word is tagged by the tag most
    frequently associated with it. OOV words are tagged by sampling from the
    distribution of all tags.

    Args:
        sentence (list): a list of tokens (the sentence to tag)
        perWordTagCounts (Counter): tags per word as specified in learn_params()
        allTagCounts (Counter): tag counts, as specified in learn_params()

        Return:
        list: list of pairs
    """
    
    tagged_sentence = []
    for word in sentence:
        if word in perWordTagCounts:
            tag = perWordTagCounts[word].most_common(1)[0][0]
        else:
            counts = np.array(list(allTagCounts.values()))
            counts = counts/sum(counts)
            all_tags = list(allTagCounts.keys())
            tag = np.random.choice(all_tags, p=counts)
        tagged_sentence.append((word,tag))
    
    
    return tagged_sentence

#===========================================
#       POS tagging with HMM
#===========================================


def hmm_tag_sentence(sentence, A, B):
    """Returns a list of pairs (w,t) where each w corresponds to a word
    (same index) in the input sentence. Tagging is done with the Viterby
    algorithm.

    Args:
        sentence (list): a list of tokens (the sentence to tag)
        A (dict): The HMM Transition probabilities
        B (dict): tthe HMM emmission probabilities.

    Return:
        list: list of pairs
    """
    viterbi_matrix = viterbi(sentence,A,B)
    tags = retrace(viterbi_matrix)
    tagged_sentence = []
    
    assert len(sentence) == len(tags)
    for i in range(len(sentence)):
        word = sentence[i]
        tag = tags[i]
        tagged_sentence.append((word,tag))

    return tagged_sentence

def viterbi(sentence, A,B):
    """Creates the Viterbi matrix, column by column. Each column is a list of
    tuples representing cells. Each cell ("item") is a tupple (t,r,p), were
    t is the tag being scored at the current position,
    r is a reference to the corresponding best item from the previous position,
    and p is a log probabilityof the sequence so far).

    The function returns the END item, from which it is possible to
    trace back to the beginning of the sentence.

    Args:
        sentence (list): a list of tokens (the sentence to tag)
        A (dict): The HMM Transition probabilities
        B (dict): tthe HMM emmission probabilities.

    Return:
        obj: the last item, tagged with END. should allow backtraking.

        """
        # Hint 1: For efficiency reasons - for words seen in training there is no
        #      need to consider all tags in the tagset, but only tags seen with that
        #      word. For OOV you have to consider all tags.
        # Hint 2: start with a dummy item  with the START tag (what would it log-prob be?).
        #         current list = [ the dummy item ]
        # Hint 3: end the sequence with a dummy: the highest-scoring item with the tag END

    sentence = [word if word in perWordTagCounts.keys() else UNK for word in sentence] + [END]
    
    states = list(allTagCounts.keys()) 
    
    viterbi_matrix = []
    for i in range(len(states)):
        row = []
        for j in range(len(sentence)-1):
            row.append((states[i],0,np.NINF))
        viterbi_matrix.append(row)
        
    
    #: initialize first column
    first_word = sentence[0]
    if first_word == UNK: 
        first_column_optional_tags = states
    else: 
        first_column_optional_tags = list(perWordTagCounts[first_word].keys())
    
    for tag in first_column_optional_tags: 
        row_index = states.index(tag)
        b_state_word = B[tag][first_word]
        try:
            transition_probability = A[START][tag]
        except:
            transition_probability = 1/sum([v for v in A[START].values()])
        
        viterbi_matrix[row_index][0] = (tag, START, transition_probability+b_state_word)
    
    for i in range(1, len(sentence[:-1])):
        word = sentence[i]
        if word == UNK: 
            optional_tags = states
        else:
            optional_tags = list(perWordTagCounts[word].keys())
        for tag in optional_tags: 
            row_index = states.index(tag)
            
            # : cut and transpose matrix
            t_viterbi_matrix = list(zip(*viterbi_matrix))
            t_viterbi_matrix_cut = t_viterbi_matrix[:i]
            
            tag, best_state_index, probability = predict_next_best(word,tag,t_viterbi_matrix_cut,A,B)
            
            viterbi_matrix[row_index][i] = (tag, best_state_index, probability)
            
    #: Add the end dummy to calculation
    t_viterbi_matrix = list(zip(*viterbi_matrix))
    tag, best_state_index, probability = predict_next_best(END,END,t_viterbi_matrix,A,B)
    viterbi_matrix.append([(tag, best_state_index, probability)])
        
    return viterbi_matrix

#a suggestion for a helper function. Not an API requirement
def retrace(viterbi_matrix):
    """Returns a list of tags (retracing the sequence with the highest probability,
        reversing it and returning the list). The list should correspond to the
        list of words in the sentence (same indices).
        
        Args: viterbi_matrix that holds all the optional paths
        
        Returns: a list of tags (retracing the sequence with the highest probability,
        reversing it and returning the list)
    """
    chosen_tags = []
    column_index = -1
    row_index = viterbi_matrix[-1][column_index][1]
    viterbi_matrix = viterbi_matrix[:-1]
    while row_index!=START:

        tag = viterbi_matrix[row_index][column_index][0]
        row_index = viterbi_matrix[row_index][column_index][1]
        column_index -= 1

        chosen_tags.append(tag)
    
    #:remove the start token and reverse the list
    chosen_tags.reverse()
    
    return chosen_tags

#a suggestion for a helper function. Not an API requirement
def predict_next_best(word, tag, viterbi_matrix,A,B):
    """Returns a new item (tupple)
    """    
    new_list = []
    for previous_tag_cell in viterbi_matrix[-1]:
        try:
            transision_proba = A[previous_tag_cell[0]][tag]
        except: #: we didn't see tag_t after tag_t-1
            transision_proba = 1/sum([v for v in A[previous_tag_cell[0]].values()])
        previous_vitarbi_path = previous_tag_cell[2]

        new_list.append(previous_vitarbi_path+transision_proba)
    max_value = max(new_list)
    #: find max probability relevant previous state index
    best_state_index = np.argmax(new_list)
    
    if tag == END:
        probability = max_value
    else:
        probability = max_value+B[tag][word]

    
    return (tag,best_state_index, probability)

def joint_prob(sentence, A, B):
    """Returns the joint probability of the given sequence of words and tags under
     the HMM model.

     Args:
         sentence (pair): a sequence of pairs (w,t) to compute.
         A (dict): The HMM Transition probabilities
         B (dict): the HMM emmission probabilities.
     """
    p = 0   # joint log prob. of words and tags

    sentence = [(START, START)] + [(word, tag) if word in perWordTagCounts.keys() else (UNK, tag) for word,tag in sentence] + [(END,END)]
    previous_tag = sentence[0][1]
    for word, tag in sentence[1:]:
        if tag in A[previous_tag]:
            p+=A[previous_tag][tag]
        else:
            p+=1/sum(list(A[previous_tag].values()))
        if tag==END:
            continue
        elif word in B[tag]:
            p+=B[tag][word] 
        else: 
            p+=1/sum(B[tag].values())
        previous_tag = tag

    assert isfinite(p) and p<0  # Should be negative. Think why!
    return p


#===========================================
#       POS tagging with BiLSTM
#===========================================

""" You are required to support two types of bi-LSTM:
    1. a vanilla biLSTM in which the input layer is based on simple word embeddings
    2. a case-based BiLSTM in which input vectors combine a 3-dim binary vector
        encoding case information, see
        https://arxiv.org/pdf/1510.06168.pdf
"""

# Suggestions and tips, not part of the required API
#
#  1. You can use PyTorch torch.nn module to define your LSTM, see:
#     https://pytorch.org/docs/stable/generated/torch.nn.LSTM.html#torch.nn.LSTM
#  2. You can have the BLSTM tagger model(s) implemented in a dedicated class
#     (this could be a subclass of torch.nn.Module)
#  3. Think about padding.
#  4. Consider using dropout layers
#  5. Think about the way you implement the input representation
#  6. Consider using different unit types (LSTM, GRU,LeRU)



class LSTMTagger(nn.Module):

    def __init__(self, embedding_dim, vocab_size, tagset_size, num_layers, hidden_dim, vocabulary):
        super(LSTMTagger, self).__init__()
        self.num_layers = num_layers
        self.hidden_dim = hidden_dim
        self.embedding_dim = embedding_dim
        self.input_dim = vocab_size
        self.output_dim = tagset_size
        self.TEXT = vocabulary[0]
        self.TAGS = vocabulary[1]
        self.CASED = vocabulary[2]
        self.dropout_rate = 0.25
        # The LSTM takes word embeddings as inputs, and outputs hidden states
        # with dimensionality hidden_dim.
        self.lstm = nn.LSTM(embedding_dim, hidden_dim, num_layers,
                            bidirectional=True,
                            dropout = self.dropout_rate)
        
        self.dropout = nn.Dropout(self.dropout_rate)

        # self.load_embeddings()
        # The linear layer that maps from hidden state space to tag space
        self.hidden2tag = nn.Linear(hidden_dim*2, tagset_size)
        

    def forward(self, sentence, cased_sentence = None):
        embeds = self.dropout(self.word_embeddings(sentence))
        if cased_sentence is not None:
            case_embeds = self.dropout(self.cased_embeddings(cased_sentence))
            embeds = torch.cat([embeds, case_embeds], dim = 2)
            
        lstm_out, self.hidden = self.lstm(embeds)
        tag_space = self.hidden2tag(self.dropout(lstm_out))
        tag_scores = tag_space
        return tag_scores
    
    def load_embeddings(self):
        pretrained_embedding = self.TEXT.vocab.vectors
        pad_ind = self.TEXT.vocab.stoi[self.TEXT.pad_token]
        self.word_embeddings = nn.Embedding(self.input_dim, self.embedding_dim)
        self.word_embeddings.weight.data.copy_(pretrained_embedding)
        self.word_embeddings.weight.data[pad_ind] = torch.zeros(self.embedding_dim)

        self.word_embeddings.weight.requires_grad = False
              
    
    def load_case_embeddings(self):
        self.lstm = nn.LSTM(self.embedding_dim+3, self.hidden_dim, self.num_layers, bidirectional=True, dropout = self.dropout_rate)
        pretrained_embedding = self.CASED.vocab.vectors
        pad_ind = self.CASED.vocab.stoi[self.CASED.pad_token]
        self.cased_embeddings = nn.Embedding(len(self.CASED.vocab), 3)
        self.cased_embeddings.weight.data.copy_(pretrained_embedding)
        self.cased_embeddings.weight.data[pad_ind] = torch.zeros(3)
        
        self.cased_embeddings.weight.requires_grad = False


def create_vocab(data_fn, min_freq, TEXT, TAGS, CASED,  vectors, max_vocab_size, input_rep):
    train_data = load_annotated_corpus(data_fn)
    unique_words = convert_to_csv_for_iterator(train_data, input_rep = input_rep)
    train = create_iterator(TEXT, TAGS,CASED, input_rep = input_rep)
    if max_vocab_size == -1: 
        TEXT.build_vocab(train, min_freq = min_freq)
    elif len(unique_words) > max_vocab_size: 
        TEXT.build_vocab(train, max_size=max_vocab_size)
    else: 
        TEXT.build_vocab(train)
    TEXT.vocab.load_vectors(vectors)
    TAGS.build_vocab(train)
    if input_rep:
        CASED = create_case_embedding(CASED, train)
    
    vocabulary  = (TEXT,TAGS, CASED)
    return vocabulary

def create_case_embedding(CASED, train):
    CASED.build_vocab(train)
    
    with open('case_vectors.txt', "w") as txt_file:
        for word, i in CASED.vocab.stoi.items():
           vec = case_based_function(word)
           line = word+" "+vec+"\n"
           txt_file.write(line)
       
    vectors =  load_pretrained_embeddings('case_vectors.txt', cached_name = 'cased_vectors')
    CASED.build_vocab(train, vectors = vectors)
        
    return CASED


def case_based_function(word):
    if word.islower():
        vec = "100"
    elif word[0].isupper():
        vec = "010"
    elif word == '<unk>':
        vec = "001"
    else:
        vec = "000"
    return vec


def convert_to_csv_for_iterator(data,data_name = "train", input_rep = 0):
    all_sentences_list = []
    all_tags_list = []
    all_cased_list =[]
    unique_words = set()
    
    for sentence in data:
        words = []
        cased = []
        tags = []
        for word,tag in sentence:
            words.append(word.lower())
            tags.append(tag)
            cased.append(word)
        unique_words.update(words)    
        words_combined = " ".join(words)
        tags_combined = " ".join(tags)
        cased_combined = " ".join(cased)
        all_sentences_list.append(words_combined)
        all_tags_list.append(tags_combined)
        all_cased_list.append(cased_combined)
    
    if input_rep:
        df = pd.DataFrame(list(zip(all_sentences_list, all_tags_list, all_cased_list)), 
                    columns =['words', 'tags', 'cased'])
        
    else: 
        df = pd.DataFrame(list(zip(all_sentences_list, all_tags_list)), 
                    columns =['words', 'tags'])
        
    df.to_csv(f"{data_name}.csv")
    
    return unique_words

def create_iterator(TEXT, TAGS,CASED, data_name = "train",input_rep = 0, ):
    
    path = f"{data_name}.csv"
    fields = [
      (None,None),     
    ('text',TEXT ), 
    ("tags", TAGS)]
    
    if input_rep:
        fields.append(('cased',CASED ))
    
    tablular_data = data.TabularDataset(path=path, format='csv', fields=fields,skip_header = True)
    
    return tablular_data


def create_data_csv_files(data, val = None, input_rep = 0):
    convert_to_csv_for_iterator(data, data_name = "train", input_rep=input_rep)
    if val:
        convert_to_csv_for_iterator(val, data_name = "val", input_rep=input_rep)

def buildTabularDataset(val_data,TEXT,TAGS,CASED, input_rep):
        
    train_data = create_iterator(TEXT, TAGS,CASED,input_rep = input_rep)
    
    if val_data:
        valid_data = create_iterator(TEXT, TAGS,CASED, data_name = "val",input_rep = input_rep)
    
    else:
        train_data,valid_data = train_data.split(split_ratio = 0.9)
            

    
    return train_data,valid_data

def init_model_weights(model):
    for name, param in model.named_parameters():
        nn.init.normal_(param.data, mean = 0, std = 0.1)


def initialize_rnn_model(params_d, hidden_dim = 128):
    """Returns a dictionary with the objects and parameters needed to run/train_rnn
       the lstm model. The LSTM is initialized based on the specified parameters.
       thr returned dict is may have other or additional fields.

    Args:
        params_d (dict): a dictionary of parameters specifying the model. The dict
                        should include (at least) the following keys:
                        {'max_vocab_size': max vocabulary size (int),
                        'min_frequency': the occurence threshold to consider (int),
                        'input_rep': 0 for the vanilla and 1 for the case-base (int),
                        'embedding_dimension': embedding vectors size (int),
                        'num_of_layers': number of layers (int),
                        'output_dimension': number of tags in tagset (int),
                        'pretrained_embeddings_fn': str,
                        'data_fn': str
                        }
                        max_vocab_size sets a constraints on the vocab dimention.
                            If the its value is smaller than the number of unique
                            tokens in data_fn, the words to consider are the most
                            frequent words. If max_vocab_size = -1, all words
                            occuring more that min_frequency are considered.
                        min_frequency privides a threshold under which words are
                            not considered at all. (If min_frequency=1 all words
                            up to max_vocab_size are considered;
                            If min_frequency=3, we only consider words that appear
                            at least three times.)
                        input_rep (int): sets the input representation. Values:
                            0 (vanilla), 1 (case-base);
                            <other int>: other models, if you are playful
                        The dictionary can include other keys, if you use them,
                             BUT you shouldn't assume they will be specified by
                             the user, so you should spacify default values.
    Return:
        a dictionary with the at least the following key-value pairs:
                                       {'lstm': torch.nn.Module object,
                                       input_rep: [0|1]}
        #Hint: you may consider adding the embeddings and the vocabulary
        #to the returned dict
    """
    
    TEXT = data.Field(lower = True)
    TAGS = data.Field(unk_token = None)
    if params_d['input_rep']: 
        CASED = data.Field(unk_token = None)
    else: 
        CASED = None 
    
    
    vectors = load_pretrained_embeddings(params_d['pretrained_embeddings_fn'])
    vocabulary = create_vocab(params_d['data_fn'], params_d['min_frequency'], TEXT, TAGS, CASED, vectors, params_d['max_vocab_size'], params_d['input_rep'])
    
    if "hidden_dim" not in params_d:  
        params_d['hidden_dim'] = hidden_dim
    
    input_dim = len(TEXT.vocab)
    lstm_model = LSTMTagger(params_d['embedding_dimension'],input_dim, params_d['output_dimension'], params_d['num_of_layers'],params_d['hidden_dim'], vocabulary = vocabulary)  
    
    lstm_model.apply(init_model_weights)
    lstm_model.load_embeddings()
    if params_d['input_rep']:
        lstm_model.load_case_embeddings()
        
    
    model = {'lstm':lstm_model, 
              'input_rep' :params_d['input_rep'],
              'embeddings': vectors, 
              'vocab': vocabulary}

    return model


def read_vocab_from_file(path):
    vectors = {}
    with open(path, 'rb') as f:
        for l in f:
            line = l.decode().split()
            word = line[0]
            vect = np.array(line[1:]).astype(np.float)
            vectors[word]=vect
        return vectors

def write_vocab_to_file(name,dict_):
    with open(f'{name}', "w") as txt_file:
        for word, i in dict_.items():
           vec = " ".join(str(x) for x in dict_[word])
           line = word+" "+vec+"\n"
           txt_file.write(line)
       

def load_pretrained_embeddings(path, vocab=None, cached_name = 'vectors'):
    """ Returns an object with the the pretrained vectors, loaded from the
        file at the specified path. The file format is the same as
        https://www.kaggle.com/danielwillgeorge/glove6b100dtxt
        You can also access the vectors at:
         https://www.dropbox.com/s/qxak38ybjom696y/glove.6B.100d.txt?dl=0
         (for efficiency (time and memory) - load only the vectors you need)
        The format of the vectors object is not specified as it will be used
        internaly in your code, so you can use the datastructure of your choice.

    Args:
        path (str): full path to the embeddings file
        vocab (list): a list of words to have embeddings for. Defaults to None.

    """
    if vocab:
        new_vocab_dict = {}
        vocab_dict = read_vocab_from_file(path)
        for word in vocab:
            word = word.lower()
            if word in vocab_dict:
                new_vocab_dict[word] = vocab_dict[word]
        path = "filterd_embddings.txt"
        write_vocab_to_file(path,new_vocab_dict)
    
    vectors = torch_vocab.Vectors(name = path, cache = cached_name, unk_init = torch.Tensor.normal_)

    return vectors

def evaluate(model, iterator, criterion, input_rep):
    
    epoch_loss = 0
    
    model.eval()
    
    with torch.no_grad():
    
        for batch in iterator:

            text = batch.text
            tags = batch.tags
            
            text = text.to(device)
            tags = tags.to(device)
            
            if input_rep: 
                cased = batch.cased
                cased = cased.to(device)
                predictions = model(text, cased)
            else: 
                predictions = model(text)
            
            
            predictions = predictions.view(-1, predictions.shape[-1])
            tags = tags.view(-1)
            
            loss = criterion(predictions, tags)
            

            epoch_loss += loss.item()
        
    return epoch_loss / len(iterator)

def train(model, iterator, optimizer, criterion, input_rep):
    
    epoch_loss = 0
    
    model.eval()
    
    model.train()
    
    for batch in tqdm(iterator):
         
        text = batch.text
        tags = batch.tags
        
        
        text = text.to(device)
        tags = tags.to(device)
        
        optimizer.zero_grad()
        
        if input_rep: 
            cased = batch.cased
            cased = cased.to(device)
            predictions = model(text, cased)
        else: 
            predictions = model(text)
       
        
        predictions = predictions.view(-1, predictions.shape[-1])
        tags = tags.view(-1)
        
        #predictions = [sent len * batch size, output dim]
        #tags = [sent len * batch size]
        
        loss = criterion(predictions, tags)
                        
        loss.backward()
        
        optimizer.step()
        
        epoch_loss += loss.item()
        
    return epoch_loss / len(iterator)

def train_rnn(model, train_data, val_data = None):
    """Trains the BiLSTM model on the specified data.

    Args:
        model (dict): the model dict as returned by initialize_rnn_model()
        train_data (list): a list of annotated sentences in the format returned
                            by load_annotated_corpus()
        val_data (list): a list of annotated sentences in the format returned
                            by load_annotated_corpus() to be used for validation.
                            Defaults to None
        input_rep (int): sets the input representation. Defaults to 0 (vanilla),
                         1: case-base; <other int>: other models, if you are playful
    """
    #Tips:
    # 1. you have to specify an optimizer
    # 2. you have to specify the loss function and the stopping criteria
    # 3. consider using batching
    # 4. some of the above could be implemented in helper functions (not part of
    #    the required API)

    BATCH_SIZE = 128
    N_EPOCHS = 20

    input_rep = model['input_rep']
    
    lstm_model = model['lstm']
    create_data_csv_files(train_data,val_data, input_rep = input_rep)
    
    TEXT,TAGS, CASED = model['vocab']
    pad_ind = TAGS.vocab.stoi[TAGS.pad_token]
    
    train_data,val_data = buildTabularDataset(val_data,TEXT,TAGS,CASED, input_rep = input_rep)
    
    train_iterator, valid_iterator = data.BucketIterator.splits(
      (train_data, val_data), sort_key=lambda x: len(x.text),
      batch_size = BATCH_SIZE,
      sort_within_batch = True)

    criterion = nn.CrossEntropyLoss(ignore_index=pad_ind) #you can set the parameters as you like
    
    # vectors = load_pretrained_embeddings(pretrained_embeddings_fn)

    lstm_model = lstm_model.to(device)
    criterion = criterion.to(device)
    optimizer = optim.Adam(lstm_model.parameters())
    

    best_valid_loss = float('inf')
    
    for epoch in range(N_EPOCHS):
        print(f"epoch: {epoch}")
        
        train_loss = train(lstm_model, train_iterator, optimizer, criterion, input_rep)
        
        valid_loss = evaluate(lstm_model, valid_iterator, criterion, input_rep)
                    
        if valid_loss < best_valid_loss:
            best_valid_loss = valid_loss
            torch.save(lstm_model.state_dict(), 'model.pt')
            print(f"best_valid_loss: {best_valid_loss}")
            
    print(f"best_valid_loss: {best_valid_loss}")
    
    
    lstm_model.load_state_dict(torch.load('model.pt'))
    model['lstm'] = lstm_model
    
def rnn_tag_sentence(sentence, model):
    """ Returns a list of pairs (w,t) where each w corresponds to a word
        (same index) in the input sentence and t is the predicted tag.

    Args:
        sentence (list): a list of tokens (the sentence to tag)
        model (dict):  a dictionary with the trained BiLSTM model and all that is needed
                        to tag a sentence.

    Return:
        list: list of pairs
    """

    TEXT, TAGS, CASED = model['vocab']
    lstm_model = model['lstm']
    input_rep = model['input_rep']
    
    vocabulary = TEXT.vocab.stoi
    vocabulary_tag = TAGS.vocab.itos
    

    sentence_lower = [[vocabulary[word.lower()] for word in sentence]]
    sentence_lower = torch.tensor(sentence_lower,dtype=torch.long)

    lstm_model = lstm_model.to(device)

    with torch.no_grad():    
        sentence_lower = sentence_lower.to(device)
        if input_rep:
            vocabulary_cased = CASED.vocab.stoi
            sentence_cased = [[vocabulary_cased[word] if word in vocabulary_cased else vocabulary[word] for word in sentence]]
            sentence_cased = torch.tensor(sentence_cased,dtype=torch.long)
            sentence_cased = sentence_cased.to(device)    
            predictions = lstm_model(sentence_lower, sentence_cased)
        else:
            predictions = lstm_model(sentence_lower)
    
    max_predict = predictions.argmax(-1)[0]
    
    predicted_tags = [vocabulary_tag[t.item()] for t in max_predict]
    
    tagged_sentence = [(word,tag) for word,tag in zip(sentence,predicted_tags)]
    
    return tagged_sentence


def get_best_performing_model_params():
    """Returns a disctionary specifying the parameters of your best performing
        BiLSTM model.
        IMPORTANT: this is a *hard coded* dictionary that will be used to create
        a model and train a model by calling
               initialize_rnn_model() and train_lstm()
    """

    
    model_params = {'max_vocab_size': 7032,
                        'min_frequency': 1,
                        'input_rep': 0,
                        'embedding_dimension': 100,
                        'num_of_layers': 2,
                        'output_dimension': 18,
                        'pretrained_embeddings_fn': "glove.6B.100d.txt",
                        'data_fn': "data/en-ud-train.upos.tsv",
                        "hidden_dim" : 128
                        }
    
    return model_params


#===========================================================
#       Wrapper function (tagging with a specified model)
#===========================================================

def tag_sentence(sentence, model):
    """Returns a list of pairs (w,t) where pair corresponds to a word (same index) in
    the input sentence. Tagging is done with the specified model.

    Args:
        sentence (list): a list of tokens (the sentence to tag)
        model (dict): a dictionary where key is the model name and the value is
           an ordered list of the parameters of the trained model (baseline, HMM)
           or the model isteld and the input_rep flag (LSTMs).

        Models that must be supported (you can add more):
        1. baseline: {'baseline': [perWordTagCounts, allTagCounts]}
        2. HMM: {'hmm': [A,B]}
        3. Vanilla BiLSTM: {'blstm':[model_dict]}
        4. BiLSTM+case: {'cblstm': [model_dict]}
        5. (NOT REQUIRED: you can add other variations, agumenting the input
            with further subword information, with character-level word embedding etc.)

        The parameters for the baseline model are:
        perWordTagCounts (Counter): tags per word as specified in learn_params()
        allTagCounts (Counter): tag counts, as specified in learn_params()

        The parameters for the HMM are:
        A (dict): The HMM Transition probabilities
        B (dict): tthe HMM emmission probabilities.

        Parameters for an LSTM: the model dictionary (allows tagging the given sentence)


    Return:
        list: list of pairs
    """
    key = list(model.keys())[0]
    values = model[key]

    
    if key == 'baseline':
        return baseline_tag_sentence(sentence, *values)
    if key == 'hmm':
        return hmm_tag_sentence(sentence, *values)
    if key == 'blstm':
        return rnn_tag_sentence(sentence, *values)
    if key == 'cblstm':
        return rnn_tag_sentence(sentence, *values)


def count_correct(gold_sentence, pred_sentence):
    """Return the total number of correctly predicted tags,the total number of
    correcttly predicted tags for oov words and the number of oov words in the
    given sentence.

    Args:
        gold_sentence (list): list of pairs, assume to be gold labels
        pred_sentence (list): list of pairs, tags are predicted by tagger

    """
    correct = 0
    correctOOV = 0
    OOV = 0
    assert len(gold_sentence)==len(pred_sentence)
    
    for gold_tuple, pred_tuple in zip(gold_sentence, pred_sentence): 
        word = gold_tuple[0]
        if gold_tuple[1]==pred_tuple[1]:
            correct +=1            
            if word not in perWordTagCounts: 
                correctOOV +=1
        else: 
            if word not in perWordTagCounts: 
                OOV +=1

    return correct, correctOOV, OOV
