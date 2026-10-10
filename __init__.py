def classFactory(iface):
    from .value_cleaner import ValueCleaner
    return ValueCleaner(iface)
