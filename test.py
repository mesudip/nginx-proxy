from nginx.ConfigParser import ConfigParser
nc = ConfigParser()
nc.load(open("test.conf").read())
print(nc.gen_config())