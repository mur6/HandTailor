# -*- coding: utf-8 -*-
"""mur6-hand_tailor.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1g0DavpmKehL0n804HAKmESIpJmFZyAk5
"""

!git clone https://github.com/mur6/HandTailor

# Commented out IPython magic to ensure Python compatibility.
# %cd HandTailor

!pip install -r requirements.txt

# Commented out IPython magic to ensure Python compatibility.
# %mkdir checkpoints

# Commented out IPython magic to ensure Python compatibility.
# %cp ../drive/MyDrive/ml/paper04/model.pt checkpoints/

# Commented out IPython magic to ensure Python compatibility.
# %cp ../drive/MyDrive/ml/MANO_RIGHT.pkl .

# Commented out IPython magic to ensure Python compatibility.
# %mkdir out

!python demo_in_the_wild.py