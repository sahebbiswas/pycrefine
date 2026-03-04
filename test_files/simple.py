#!python3

"""
File docstring
"""



def show_info(x : int) -> int :
    """
    some doc string
    """
    for i in range(x):
        print(2*i)
        
    return 2%x == 0
    

if __name__=="__main__":
    show_info(39)
    show_info(90)