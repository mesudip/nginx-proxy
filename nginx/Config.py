class Block:
    def __init__(self):
        self.parameters=[]
        self.contents=[]


class Direction:
    def __init__(self,name,value):
        self.name = name
        if type(value) in (tuple,list):
            self.values=value
        else:
            self.values=[value]
