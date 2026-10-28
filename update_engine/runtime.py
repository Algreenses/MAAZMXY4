"""The embedded Python layout shared by setup, release and update validation."""
PYTHON_REQUIRED = (
    'python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'DLLs',
    'Lib/os.py', 'Lib/site-packages/maa/__init__.py', 'Lib/site-packages/numpy/__init__.py',
    'Lib/site-packages/strenum/__init__.py', 'Lib/site-packages/pynput/__init__.py',
    'Lib/site-packages/six.py', 'Lib/site-packages/MaaAgentBinary', 'Lib/tkinter/__init__.py',
    'DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll',
    'tcl/tcl8.6/init.tcl', 'tcl/tk8.6/tk.tcl',
)
PYTHON_DIRECTORIES = frozenset(('DLLs', 'Lib/site-packages/MaaAgentBinary'))
