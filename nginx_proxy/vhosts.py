from nginx.ConfigParser import ConfigParser as Parser


def get_vhost_as_template(vhost_file: str):
    parser = Parser()
    parser.loadf(vhost_file)
    b = 3
    print(str(parser.data))
    parser.loadf(vhost_file)
