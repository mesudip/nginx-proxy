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
from nginx.Config import *

class ConfigParser:
    def __init__(self, offset_char=' '):
        self.i = 0  # char iterator for parsing
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

    def load(self, config):
        self.config = config
        self.length = len(config) - 1
        self.i = 0
        self.data = self.parse_block("","")

    def loadf(self, filename):
        with open(filename, 'r') as f:
            conf = f.read()
            self.load(conf)

    def savef(self, filename):
        with open(filename, 'w') as f:
            conf = self.gen_config()
            f.write(conf)

    def parse_block(self,name,parameters):
        data = []
        param_name = None
        param_value = None
        buf = ''
        block=Block(name,parameters,[])
        while self.i < self.length:
            if self.config[self.i] == '\n':  # multiline value
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
                    block.append(Direction(param_name,param_value))
                else:
                    block.append(Direction(param_value,[]))
                param_name = None
                param_value = None
                buf = ''
            elif self.config[self.i] == '{':
                self.i += 1
                _block = self.parse_block(param_name,"")
                _block.parameters=buf.strip()
                block.append(_block)
                param_name = None
                param_value = None
                buf = ''
            elif self.config[self.i] == '}':
                self.i += 1
                return block
            elif self.config[self.i] == '#':  # skip comments
                while self.i < self.length and self.config[self.i] != '\n':
                    self.i += 1
            else:
                buf += self.config[self.i]
            self.i += 1
        return block

    def gen_block(self, blocks, offset):
        subrez = ''  # ready to return string
        block_name = None
        block_param = ''
        for i, block in enumerate(blocks):
            if isinstance(block, tuple):
                if len(block) == 1 and type(block[0]) == str:  # single param
                    subrez += self.off_char * offset + '%s;\n' % (block[0])
                elif isinstance(block[1], str):
                    subrez += self.off_char * offset + '%s %s;\n' % (block[0], block[1])
                else:  # multiline
                    subrez += self.off_char * offset + '%s %s;\n' % (block[0],
                                                                     self.gen_block(block[1], offset + len(
                                                                         block[0]) + 1).rstrip())

            elif isinstance(block, dict):
                block_value = self.gen_block(block['value'], offset + 4)
                if block['param']:
                    param = block['param'] + ' '
                else:
                    param = ''
                if subrez != '':
                    subrez += '\n'
                subrez += '%(offset)s%(name)s %(param)s{\n%(data)s%(offset)s}\n' % {
                    'offset': self.off_char * offset,
                    'name': block['name'],
                    'data': block_value,
                    'param': param}

            elif isinstance(block, str):  # multiline params
                if i == 0:
                    subrez += '%s\n' % block
                else:
                    subrez += '%s%s\n' % (self.off_char * offset, block)

        if block_name:
            return '%(offset)s%(name)s %(param)s{\n%(data)s%(offset)s}\n' % {
                'offset': self.off_char * offset, 'name': block_name, 'data': subrez,
                'param': block_param}
        else:
            return subrez

    def gen_config(self, offset_char=' '):
        self.off_char = offset_char
        return self.gen_block(self.data, 0)
