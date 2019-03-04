'''
======================================================================================================
Copyright (c) 2013, Makarov Yurii

               All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
======================================================================================================
'''

class ConfigParser:
    def __init__(self, offset_char=' '):
        self.i = 0 #char iterator for parsing
        self.length = 0
        self.config = ''
        self.data = []
        self.off_char = offset_char

    def __getitem__(self, index):
        return self.data[index]

    def __setitem__(self, index, value):
        self.data[index] = value

    def __delitem__(self, index):
        del self.data[index]

    def __call__(self):
        return self.gen_config()

    def get_value(self, data):
        if isinstance(data, tuple):
            return data[1]
        elif isinstance(data, dict):
            return data['value']
        else:
            return data

    def get_name(self, data):
        if isinstance(data, tuple):
            return data[0]
        elif isinstance(data, dict):
            return data['name']
        else:
            return data

    def set(self, item_arr, value=None, param=None, name=None):
        if isinstance(item_arr, str):
            elem = item_arr
            parent = self.data
        elif isinstance(item_arr, list) and len(item_arr) == 1:
            elem = item_arr[0]
            parent = self.data
        else:
            elem = item_arr.pop()
            parent = self.get_value(self.get(item_arr))

        if parent is None:
            raise KeyError('No such block.')

        if isinstance(elem, str) and isinstance(value, str):
            #modifying text parameter
            for i, param in enumerate(parent):
                if isinstance(param, tuple):
                    if param[0] == elem:
                        if value is not None and name is not None:
                            parent[i] = (name, value)
                            return
                        elif value is not None:
                            parent[i] = (param[0], value)
                            return
                        elif name is not None:
                            parent[i] = (name, param[1])
                            return
                        raise TypeError('Not expected value type')

        elif isinstance(elem, tuple):
            #modifying block
            if len(elem) == 1:
                elem = (elem[0], '')
            for i, block in enumerate(parent):
                if isinstance(block, dict):
                    if elem == (block['name'], block['param']):
                        if value is not None and isinstance(value, list):
                            parent[i]['value'] = value
                            return
                        if param is not None and isinstance(param, str):
                            parent[i]['param'] = param
                            return
                        if name is not None and isinstance(name, str):
                            parent[i]['name'] = name
                            return
                        raise TypeError('Not expected value type')
        raise KeyError('No such parameter.')

    def get(self, item_arr, data=[]):
        if data == []:
            data = self.data
        if type(item_arr) in [str, tuple]:
            item = item_arr
        elif isinstance(item_arr, list):
            if len(item_arr) == 1:
                item = item_arr[0]
            else:
                element = item_arr.pop(0)
                if isinstance(element, tuple):#cannot be a string
                    if len(element) == 1:
                        element = (element[0], '')
                    for i, data_elem in enumerate(data):
                        if isinstance(data_elem, dict):
                            if (data_elem['name'], data_elem['param']) == element:
                                return self.get(item_arr, self.get_value(data[i]))

        if not 'item' in locals():
            raise KeyError('Error while getting parameter.')
        if isinstance(item, str):
            for i, elem in enumerate(data):
                if isinstance(elem, tuple):
                    if elem[0] == item:
                        return data[i]
        elif isinstance(item, tuple):
            if len(item) == 1:
                item = (item[0], '')
            for i, elem in enumerate(data):
                if isinstance(elem, dict):
                    if (elem['name'], elem['param']) == item:
                        return data[i]
        return None

    def append(self, item, root=[], position=None):
        if root == []:
            root = self.data
        elif root is None:
            raise AttributeError('Root element is None')
        if position:
            root.insert(position, item)
        else:
            root.append(item)

    def remove(self, item_arr, data=[]):
        if data == []:
            data = self.data
        if type(item_arr) in [str, tuple]:
            item = item_arr
        elif isinstance(item_arr, list):
            if len(item_arr) == 1:
                item = item_arr[0]
            else:
                elem = item_arr.pop(0)
                if type(elem) in [tuple,str]:
                    self.remove(item_arr, self.get_value(self.get(elem, data)))
                    return

        if isinstance(item, str):
            for i,elem in enumerate(data):
                if isinstance(elem, tuple):
                    if elem[0] == item:
                        del data[i]
                        return
        elif isinstance(item, tuple):
            if len(item) == 1:
                item = (item[0], '')
            for i,elem in enumerate(data):
                if isinstance(elem, dict):
                    if (elem['name'], elem['param']) == item:
                        del data[i]
                        return
        else:
            raise AttributeError("Unknown item type '%s' in item_arr" % item.__class__.__name__)
        raise KeyError('Unable to remove')

    def load(self, config):
        self.config = config
        self.length = len(config) - 1
        self.i = 0
        self.data = self.parse_block()

    def loadf(self, filename):
        with open(filename, 'r') as f:
            conf = f.read()
            self.load(conf)

    def savef(self, filename):
        with open(filename, 'w') as f:
            conf = self.gen_config()
            f.write(conf)

    def parse_block(self):
        data = []
        param_name = None
        param_value = None
        buf = ''
        while self.i < self.length:
            if self.config[self.i] == '\n': #multiline value
                if buf and param_name:
                    if param_value is None:
                        param_value = []
                    param_value.append(buf.strip())
                    buf = ''
            elif self.config[self.i] == ' ':
                if not param_name and len(buf.strip()) > 0:
                    param_name = buf.strip()
                    buf = ''
                else:
                    buf += self.config[self.i]
            elif self.config[self.i] == ';':
                if isinstance(param_value, list):
                    param_value.append(buf.strip())
                else:
                    param_value = buf.strip()
                if param_name:
                    data.append((param_name, param_value))
                else:
                    data.append((param_value,))
                param_name = None
                param_value = None
                buf = ''
            elif self.config[self.i] == '{':
                self.i += 1
                block = self.parse_block()
                data.append({'name':param_name, 'param':buf.strip(), 'value':block})
                param_name = None
                param_value = None
                buf = ''
            elif self.config[self.i] == '}':
                self.i += 1
                return data
            elif self.config[self.i] == '#': #skip comments
                while self.i < self.length and self.config[self.i] != '\n':
                    self.i += 1
            else:
                buf += self.config[self.i]
            self.i += 1
        return data

    def gen_block(self, blocks, offset):
        subrez = '' # ready to return string
        block_name = None
        block_param = ''
        for i, block in enumerate(blocks):
            if isinstance(block, tuple):
                if len(block) == 1 and type(block[0]) == str: #single param
                    subrez += self.off_char * offset + '%s;\n' % (block[0])
                elif isinstance(block[1], str):
                    subrez += self.off_char * offset + '%s %s;\n' % (block[0], block[1])
                else: #multiline
                    subrez += self.off_char * offset + '%s %s;\n' % (block[0],
                        self.gen_block(block[1], offset + len(block[0]) + 1).rstrip())

            elif isinstance(block, dict):
                block_value = self.gen_block(block['value'], offset + 4)
                if block['param']:
                    param = block['param'] + ' '
                else:
                    param = ''
                if subrez != '':
                    subrez += '\n'
                subrez += '%(offset)s%(name)s %(param)s{\n%(data)s%(offset)s}\n' % {
                    'offset':self.off_char * offset, 'name':block['name'], 'data':block_value,
                    'param':param}

            elif isinstance(block, str): #multiline params
                if i == 0:
                    subrez += '%s\n' % block
                else:
                    subrez += '%s%s\n' % (self.off_char * offset, block)

        if block_name:
            return '%(offset)s%(name)s %(param)s{\n%(data)s%(offset)s}\n' % {
                'offset':self.off_char * offset, 'name':block_name, 'data':subrez,
                'param':block_param}
        else:
            return subrez

    def gen_config(self, offset_char=' '):
        self.off_char = offset_char
        return self.gen_block(self.data, 0)
