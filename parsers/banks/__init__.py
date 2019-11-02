class Parsers(dict):
    def __init__(self):
        from importlib import import_module
        import inspect
        from os.path import join
        from os import walk
        _, _, filenames = next(walk(__path__[0]))
        for f in filenames:
            if f == '__init__.py':
                continue
            mod = inspect.getmodulename(join(__path__[0], f))
            if mod is None:
                continue
            mod = import_module('.'+mod, __name__)
            for elem in dir(mod):
                elem = getattr(mod, elem)
                if (inspect.isclass(elem)
                    and getattr(elem, 'bank_folder', None) is not None):
                        self.add_format(elem)

    def add_format(self, format_class):
        key = format_class.bank_folder
        self[key] = format_class

parsers = Parsers()

__all__ = ['parsers']
