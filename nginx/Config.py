import re


class ConfigNode:
    def __init__(self):
        self._parent_block = None
        self.offset_char = " "

    def is_block(self):
        return False

    def is_direction(self):
        return False


class Block(ConfigNode):
    def __init__(self, name, parameters, contents):
        super().__init__()
        self.name = name
        self.parameters = parameters
        self.contents = contents

    def __del__(self):
        if self._parent_block:
            self._parent_block.delete(self)

    def append(self, data):
        data._parent_block = self
        self.contents.append(data)

    def is_block(self):
        return True

    def is_direction(self):
        return False

    def delete(self, content):
        self.contents.remove(content)

    def __str__(self, offset=-1, sep="  ", gen_block_name=False):

        data = "".join([c.__str__(offset=offset + 1, sep=sep, gen_block_name=True) for c in self.contents])
        if gen_block_name:
            return "%(offset)s%(name)s %(param)s {\n%(data)s%(offset)s}\n" % {
                "offset": sep * offset,
                "name": self.name,
                "data": data,
                "param": " ".join(self.parameters) if type(self.parameters) is not str else self.parameters,
            }
        else:
            return data

    def __len__(self):
        return len(self.contents)

    def __getitem__(self, item):
        if type(item) is str:
            for x in self.contents:
                if x.name == item:
                    yield x
        if type(item) is tuple:
            for x in self.contents:
                if x.name == item[0]:
                    type(x is Block)

    def __setitem__(self, key, value):
        pass

    def __delitem__(self, key):
        pass

    def __iter__(self):
        pass

    def __contains__(self, item):
        pass

    def __repr__(self):
        return self.name + "{}"


class Direction(ConfigNode):
    def __init__(self, name, value):
        super().__init__()
        self.name = name
        if type(value) in (tuple, list):
            self.values = [x for x in value]
        else:
            self.values = [value]

    def __del__(self):
        if self._parent_block:
            self._parent_block.delete(self)

    def __hash__(self):
        return hash(self.name, self.values)

    def is_block(self):
        return False

    def is_direction(self):
        return True

    def __str__(self, offset=0, __values=None, sep="  ", gen_block_name=False):

        return sep * offset + self.name + " " + " ".join(self.values) + ";\n"
        # if not __values:
        #     block = self.values
        # else:
        #     block = __values
        # if isinstance(block, tuple):
        #     if len(block) == 1 and type(block[0]) == str:  # single param
        #         return sep * offset + '%s;\n' % (block[0])
        #     elif isinstance(block[1], str):
        #         return sep * offset + '%s %s;\n' % (block[0], block[1])
        #     else:  # multiline
        #         return sep * offset + '%s %s;\n' % (block[0],
        #                                                          self.__str__(block[1], offset + len(
        #                                                              block[0]) + 1).rstrip())

    def __repr__(self):
        return "<" + self.name + ">"
