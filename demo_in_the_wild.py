import cv2
import numpy as np
import pygame
import torch
import time
import torch.backends.cudnn as cudnn

import jax.numpy as npj
import PIL.Image as Image
import glob
from jax import grad, jit, vmap
from jax.experimental import optimizers
from torchvision.transforms import functional
import pickle

from manolayer import ManoLayer
from model import HandNetInTheWild
from checkpoints import CheckpointIO
import utils

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
cudnn.benchmark = True

mano_layer = ManoLayer(center_idx=9, side="right", mano_root=".", use_pca=False, flat_hand_mean=True,)
mano_layer = jit(mano_layer)

@jit
def hm_to_kp2d(hm):
    b, c, w, h = hm.shape
    hm = hm.reshape(b,c,-1)
    hm = hm/npj.sum(hm,-1,keepdims=True)
    coord_map_x = npj.tile(npj.arange(0,w).reshape(-1,1), (1,h))
    coord_map_y = npj.tile(npj.arange(0,h).reshape(1,-1), (w,1))
    coord_map_x = coord_map_x.reshape(1,1,-1)
    coord_map_y = coord_map_y.reshape(1,1,-1)
    x = npj.sum(coord_map_x * hm,-1,keepdims=True)
    y = npj.sum(coord_map_y * hm,-1,keepdims=True)
    kp_2d = npj.concatenate((y,x),axis=-1)
    return kp_2d

@jit
def reinit_scale(joint,kp2d,t):
    xy = joint[:,:2]
    kp2d = kp2d[0]
    s1 = npj.sum(xy*(kp2d-t))
    s2 = npj.sum((xy**2))
    s = s1/s2
    #bone = bone * npj.max([s,0.9])
    return s

@jit
def geo(joint):
    idx_a = npj.array([1,5,9,13,17])
    idx_b = npj.array([2,6,10,14,18])
    idx_c = npj.array([3,7,11,15,19])
    idx_d = npj.array([4,8,12,16,20])
    p_a = joint[:,idx_a,:]
    p_b = joint[:,idx_b,:]
    p_c = joint[:,idx_c,:]
    p_d = joint[:,idx_d,:]
    v_ab = p_a - p_b #(B, 5, 3)
    v_bc = p_b - p_c #(B, 5, 3)
    v_cd = p_c - p_d #(B, 5, 3)
    loss_1 = npj.abs(npj.sum(npj.cross(v_ab, v_bc, -1) * v_cd, -1)).mean()
    loss_2 = - npj.clip(npj.sum(npj.cross(v_ab, v_bc, -1) * npj.cross(v_bc, v_cd, -1)), -npj.inf, 0).mean()
    loss = 10000*loss_1 + 100000*loss_2

    return loss

@jit
def residuals(input_list,so3_init,beta_init,kp2d,s,t):
    so3 = input_list['so3']
    beta = input_list['beta']
    so3 = so3[npj.newaxis,...]
    beta = beta[npj.newaxis,...]
    _, joint_mano, _ = mano_layer(
        pose_coeffs = so3,
        betas = beta
    )
    bone_pred = npj.linalg.norm(joint_mano[:, 0, :] - joint_mano[:, 9, :], axis=1, keepdims=True)
    bone_pred = bone_pred[:,npj.newaxis,...]
    reg = ((so3 - so3_init)**2)
    reg_beta = ((beta - beta_init)**2)
    joint_mano = joint_mano / bone_pred
    geo_reg = geo(joint_mano)
    xy = joint_mano[...,:2]
    uv = ( xy * s ) + t
    errkp = ((uv - kp2d)**2)
    err = 0.01*reg.mean() + 0.01*reg_beta.mean() + 1*errkp.mean() + 100*geo_reg.mean()
    return err

@jit
def mano_de(so3, beta):
    verts_mano, joint_mano, _ = mano_layer(
        pose_coeffs = so3[npj.newaxis,...],
        betas = beta[npj.newaxis,...]
    )

    bone_pred = npj.linalg.norm(joint_mano[:, 0, :] - joint_mano[:, 9, :],axis=1, keepdims=True)
    bone_pred = bone_pred[:,npj.newaxis,...]
    verts_mano = verts_mano / bone_pred
    v = verts_mano[0]
    return v

@jit
def mano_de_j(so3, beta):
    _, joint_mano, _ = mano_layer(
        pose_coeffs = so3[npj.newaxis,...],
        betas = beta[npj.newaxis,...]
    )

    bone_pred = npj.linalg.norm(joint_mano[:, 0, :] - joint_mano[:, 9, :],axis=1, keepdims=True)
    bone_pred = bone_pred[:,npj.newaxis,...]
    joint_mano = joint_mano / bone_pred
    j = joint_mano[0]
    return j

def live_application():
    model = HandNetInTheWild()
    model = model.to(device)
    checkpoint_io = CheckpointIO('.', model=model)
    load_dict = checkpoint_io.load('checkpoints/model.pt')
    model.eval()

    dd = pickle.load(open("MANO_RIGHT.pkl", 'rb'), encoding='latin1')
    face = np.array(dd['f'])
    renderer = utils.OpendrRenderer(
                img_size=256)
    
    gr = jit(grad(residuals))
    lr = 0.03
    opt_init, opt_update, get_params = optimizers.adam(lr, b1=0.5, b2=0.5)
    opt_init = jit(opt_init)
    opt_update = jit(opt_update)
    get_params = jit(get_params)
    i = 0
    img_list = glob.glob("./demo2/*.jpeg")
    with torch.no_grad():
        for img_path in img_list:
            i = i + 1
            img = np.array(Image.open(img_path))
            if img is None:
                continue
            
            if img.shape[0] > img.shape[1]:
                margin = int((img.shape[0] - img.shape[1]) / 2)
                img = img[margin:-margin]
            elif img.shape[0] < img.shape[1]:
                margin = int((img.shape[1] - img.shape[0]) / 2)
                img = img[:, margin:-margin]
            img = cv2.resize(img, (256, 256),cv2.INTER_LINEAR)
            frame = img.copy()

            img = functional.to_tensor(img).float()
            img = functional.normalize(img, [0.5, 0.5, 0.5], [1, 1, 1])
            img = img.unsqueeze(0).to(device)
            
            hm, so3, beta, joint_root, bone = model(img)
            kp2d = hm_to_kp2d(hm.detach().cpu().numpy())*4
            t = kp2d[0,9,:]
            so3 = so3[0].detach().cpu().float().numpy()
            beta = beta[0].detach().cpu().float().numpy()
            bone = bone[0].detach().cpu().numpy()
            joint_root = joint_root[0].detach().cpu().numpy()
            so3 = npj.array(so3)
            beta = npj.array(beta)
            bone = npj.array(bone)
            joint_root = npj.array(joint_root)
            kp2d = npj.array(kp2d)
            so3_init = so3
            beta_init = beta
            joint = mano_de_j(so3, beta)
            s = reinit_scale(joint,kp2d,t)
            params = {'so3':so3, 'beta':beta}
            so3 = params['so3']
            beta = params['beta']
            opt_state = opt_init(params)
            
            n = 0
            while n < 20:
                n = n + 1
                params = get_params(opt_state)
                grads = gr(params,so3_init,beta_init,kp2d,s,t)
                opt_state = opt_update(n, grads, opt_state)
                joint = mano_de_j(so3, beta)
                s = reinit_scale(joint,kp2d,t)
            params = get_params(opt_state)
            so3 = params['so3']
            beta = params['beta']
            v = mano_de(so3, beta)
            v = v*s + np.array([t[0],t[1],0])
            frame1 = renderer.render(v.copy(), face, frame)
            cv2.imwrite("./out/" + str(i) + "_input.png", np.flip(frame,-1))
            cv2.imwrite("./out/" + str(i) + "_output.png", np.flip(frame1,-1))

if __name__ == '__main__':
    live_application()
