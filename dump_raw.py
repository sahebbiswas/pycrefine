import sys, pycrefine
d = pycrefine.get_decompiler('test_files/simple.cpython-39.pyc')
code = d.code_obj.co_code
with open('raw.json', 'w') as f:
    f.write('[\n')
    for i in range(0, len(code), 2):
        f.write(f'  {{"offset": {i}, "opcode": {code[i]}, "arg": {code[i+1]}}},\n')
    f.write(']\n')
