"""
155mm PGK Artillery Simulation  -  v4  (SIH 2026)
====================================================
Team   : Vector Victims
Problem: PS26098  |  YIL / Ministry of Defence

pip install pygame numpy matplotlib

Controls:
  SPACE / FIRE     launch shell
  G                toggle Guidance ON/OFF
  RightDrag        move target mid-flight
  LeftDrag(3D)     orbit camera (with inertia)
  Scroll           zoom
  R                reset camera
  A                auto-fire
  C                clear all
  M                toggle mini-map
  F11              fullscreen toggle
  ESC              quit
"""

import pygame, math, random, sys, threading
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from io import BytesIO
from collections import deque

# ======== CONSTANTS ========
W, H            = 1366, 768
PANEL_W         = 260
VIEW_W          = 660
VIEW_H          = H
GRAPH_X         = PANEL_W + VIEW_W
GRAPH_W_DEFAULT = W - PANEL_W - VIEW_W
GRAPH_H_DEFAULT = H
FPS             = 60
G               = 9.81
ARMING_DIST     = 100.0
TRAIL_MAXLEN    = 350
TRAIL_DRAW_STEP = 3
MAX_SHELLS      = 20
GRAPH_REFRESH   = 0.30
ARC_STEPS       = 1200
ARC_DT          = 0.12
CAMERA_FOV_DEG  = 24
CAMERA_DAMPING  = 0.82
CAM_SENS_YAW    = 0.32
CAM_SENS_PITCH  = 0.24
SCROLL_SPEED    = 22
SLIDER_LERP     = 14.0
WIND_SCALE      = 0.025

CB  = ( 10, 14, 28); CP  = ( 18, 24, 44); CS  = ( 38, 50, 85)
CW  = (255,255,255); CGR = (120,132,160); CDK = ( 45, 55, 82)
CY  = (255,210, 45); CR  = (220, 55, 55); CG  = ( 50,200, 90)
CCY = ( 60,215,210); COR = (255,145, 35); CPK = (220, 75,180)
CGD = ( 22, 42, 22); CST = ( 12, 20, 55); CSB = ( 28, 45, 90)
C_STEEL  = (160,165,175); C_GOLD   = (180,150, 50)
C_COPPER = (184, 92, 32); C_DARK   = ( 70, 75, 85)
C_NOSE   = (140,145,155); C_PCB    = ( 40,120, 60)

SKY_SURF    = None
_DISK_SURFS = None
_arc_cache  = {"pts_u": [], "land_u": None, "params": None}


# ======== AudioSystem ========
class AudioSystem:
    SR = 22050
    def __init__(self):
        self.vol_master=0.7; self.vol_sfx=0.8; self.vol_music=0.4
        try:
            pygame.mixer.pre_init(self.SR, -16, 1, 512)
            pygame.mixer.init()
            self._ok = True
        except Exception:
            self._ok = False; return
        self._cannon  = self._make_sound(self._mk_cannon())
        self._whistle = self._make_sound(self._mk_whistle())
        self._impact  = self._make_sound(self._mk_impact())
        self._ambient = self._make_sound(self._mk_ambient())
        self._amb_ch  = pygame.mixer.Channel(0)
        self._sfx_ch  = pygame.mixer.Channel(1)
        self._whi_ch  = pygame.mixer.Channel(2)
        if self._ambient:
            self._amb_ch.set_volume(self.vol_music*self.vol_master)
            self._amb_ch.play(self._ambient, loops=-1)

    def _arr(self, a):
        a = a / max(np.max(np.abs(a)), 1e-6)
        return (a*32767).astype(np.int16)

    def _make_sound(self, a):
        try: return pygame.sndarray.make_sound(self._arr(a))
        except: return None

    def _mk_cannon(self):
        n=int(self.SR*0.38); t=np.linspace(0,0.38,n)
        noise=np.random.randn(n)*0.55
        thump=np.sin(2*np.pi*55*t)*np.exp(-t*10)
        body =np.sin(2*np.pi*120*t)*0.3*np.exp(-t*18)
        return ((noise+thump+body)*np.exp(-t*5)).clip(-1,1)

    def _mk_whistle(self):
        n=int(self.SR*3.0); t=np.linspace(0,3.0,n)
        freq=900*np.exp(-t*0.7)+80
        ph=2*np.pi*np.cumsum(freq)/self.SR
        return (np.sin(ph)*np.exp(-t*0.35)*0.4).clip(-1,1)

    def _mk_impact(self):
        n=int(self.SR*0.7); t=np.linspace(0,0.7,n)
        noise=np.random.randn(n)*np.exp(-t*9)
        rumble=np.sin(2*np.pi*38*t)*np.exp(-t*3.5)
        crack=np.sin(2*np.pi*200*t)*0.4*np.exp(-t*20)
        return ((noise+rumble+crack)*0.8).clip(-1,1)

    def _mk_ambient(self):
        n=int(self.SR*4.0); t=np.linspace(0,4.0,n)
        return (np.sin(2*np.pi*36*t)*0.28+np.sin(2*np.pi*50*t)*0.16+
                np.sin(2*np.pi*74*t)*0.08+np.sin(2*np.pi*22*t)*0.12).clip(-1,1)

    def play_fire(self):
        if not self._ok: return
        if self._cannon:
            self._sfx_ch.set_volume(self.vol_sfx*self.vol_master)
            self._sfx_ch.play(self._cannon)
        if self._whistle:
            self._whi_ch.set_volume(self.vol_sfx*self.vol_master*0.5)
            self._whi_ch.play(self._whistle)

    def play_impact(self):
        if not self._ok: return
        try:
            ch=pygame.mixer.find_channel(True)
            if ch and self._impact:
                ch.set_volume(self.vol_sfx*self.vol_master)
                ch.play(self._impact)
        except: pass

    def stop_whistle(self):
        if self._ok: self._whi_ch.stop()

    def set_volumes(self, master, sfx, music):
        self.vol_master=master; self.vol_sfx=sfx; self.vol_music=music
        if self._ok: self._amb_ch.set_volume(music*master)


# ======== Camera ========
class Camera:
    def __init__(self):
        self.yaw=28.0; self.pitch=20.0; self.dist=700.0
        self.cx=0.0; self.cy=0.0; self.cz=500.0
        self.yaw_vel=0.0; self.pitch_vel=0.0
        self._drag=False; self._last=None; self._dirty=True
        self._ex=self._ey=self._ez=0.0
        self._rx=self._ry2=self._rz=0.0
        self._ux=self._uy=self._uz=0.0
        self._fx=self._fy=self._fz=0.0
        self._f=0.0

    def _update_matrix(self):
        if not self._dirty: return
        ry=math.radians(self.yaw); rp=math.radians(self.pitch)
        self._ex=self.cx+self.dist*math.cos(rp)*math.sin(ry)
        self._ey=self.cy+self.dist*math.sin(rp)
        self._ez=self.cz+self.dist*math.cos(rp)*math.cos(ry)
        fx=self.cx-self._ex; fy=self.cy-self._ey; fz=self.cz-self._ez
        fl=math.sqrt(fx*fx+fy*fy+fz*fz)
        if fl<1e-6: self._dirty=False; return
        fx/=fl; fy/=fl; fz/=fl
        self._fx=fx; self._fy=fy; self._fz=fz
        rx=-fz; rz=fx
        rl=math.sqrt(rx*rx+rz*rz)
        if rl<1e-6: self._dirty=False; return
        rx/=rl; rz/=rl
        self._rx=rx; self._ry2=0.0; self._rz=rz
        self._ux=-rz*fy; self._uy=rz*fx-rx*fz; self._uz=rx*fy
        self._f=1.0/math.tan(math.radians(CAMERA_FOV_DEG))
        self._dirty=False

    def project(self, wx, wy, wz):
        self._update_matrix()
        dx=wx-self._ex; dy=wy-self._ey; dz=wz-self._ez
        cx2=dx*self._rx+dy*self._ry2+dz*self._rz
        cy2=dx*self._ux+dy*self._uy+dz*self._uz
        cz2=dx*self._fx+dy*self._fy+dz*self._fz
        if cz2<0.5: return None
        sx=PANEL_W+VIEW_W/2+(cx2/cz2)*self._f*VIEW_H/2
        sy=VIEW_H/2-(cy2/cz2)*self._f*VIEW_H/2
        return (int(sx),int(sy),cz2)

    def update(self):
        if abs(self.yaw_vel)>0.01 or abs(self.pitch_vel)>0.01:
            self.yaw+=self.yaw_vel; self.pitch+=self.pitch_vel
            self.yaw_vel*=CAMERA_DAMPING; self.pitch_vel*=CAMERA_DAMPING
            self.pitch=max(5,min(78,self.pitch)); self._dirty=True

    def handle(self, ev):
        in_v=lambda p: PANEL_W<p[0]<PANEL_W+VIEW_W
        if ev.type==pygame.MOUSEBUTTONDOWN and ev.button==1 and in_v(ev.pos):
            self._drag=True; self._last=ev.pos
        if ev.type==pygame.MOUSEBUTTONUP and ev.button==1: self._drag=False
        if ev.type==pygame.MOUSEMOTION and self._drag and self._last:
            dx=ev.pos[0]-self._last[0]; dy=ev.pos[1]-self._last[1]
            self.yaw_vel-=dx*CAM_SENS_YAW; self.pitch_vel+=dy*CAM_SENS_PITCH
            self._last=ev.pos; self._dirty=True
        if ev.type==pygame.MOUSEWHEEL and PANEL_W<pygame.mouse.get_pos()[0]<PANEL_W+VIEW_W:
            self.dist=max(80,min(3000,self.dist-ev.y*SCROLL_SPEED)); self._dirty=True

    def reset(self):
        self.yaw=28.0; self.pitch=20.0; self.dist=700.0
        self.cx=0.0; self.cy=0.0; self.cz=500.0
        self.yaw_vel=0.0; self.pitch_vel=0.0; self._dirty=True


# ======== Slider ========
class Slider:
    def __init__(self, x, y, w, lbl, mn, mx, val, unit="", fmt=".0f", col=CCY):
        self.rx=x; self.ry=y; self.rw=w; self.label=lbl
        self.vmin=float(mn); self.vmax=float(mx)
        self.value=float(val); self.target_value=float(val)
        self.unit=unit; self.fmt=fmt; self.color=col; self.th=8; self.drag=False

    def frac(self): return (self.value-self.vmin)/(self.vmax-self.vmin)
    def thumb(self): return (int(self.rx+self.frac()*self.rw),self.ry+3)

    def update(self, dt):
        alpha=min(1.0, dt*SLIDER_LERP)
        self.value+=(self.target_value-self.value)*alpha

    def draw(self, surf, fsm, fxs):
        surf.blit(fsm.render(self.label,True,CW),(self.rx,self.ry-19))
        vt=fsm.render(format(self.value,self.fmt)+" "+self.unit,True,self.color)
        surf.blit(vt,(self.rx+self.rw-vt.get_width(),self.ry-19))
        pygame.draw.rect(surf,CDK,(self.rx,self.ry,self.rw,5),border_radius=3)
        fw=int(self.frac()*self.rw)
        if fw>0: pygame.draw.rect(surf,self.color,(self.rx,self.ry,fw,5),border_radius=3)
        tx,ty=self.thumb()
        pygame.draw.circle(surf,CW,(tx,ty),self.th)
        pygame.draw.circle(surf,self.color,(tx,ty),self.th-3)
        surf.blit(fxs.render(f"{self.vmin:.0f}",True,CGR),(self.rx,self.ry+8))
        mx2=fxs.render(f"{self.vmax:.0f}",True,CGR)
        surf.blit(mx2,(self.rx+self.rw-mx2.get_width(),self.ry+8))

    def handle(self, ev):
        tx,ty=self.thumb()
        if ev.type==pygame.MOUSEBUTTONDOWN:
            if math.hypot(ev.pos[0]-tx,ev.pos[1]-ty)<self.th+5: self.drag=True
        if ev.type==pygame.MOUSEBUTTONUP: self.drag=False
        if ev.type==pygame.MOUSEMOTION and self.drag:
            f=(ev.pos[0]-self.rx)/self.rw
            self.target_value=self.vmin+max(0.0,min(1.0,f))*(self.vmax-self.vmin)


# ======== Button ========
class Button:
    def __init__(self, x, y, w, h, text, nc=(35,110,55), hc=(50,155,75), pc=(20,75,38)):
        self.rect=pygame.Rect(x,y,w,h); self.text=text
        self.nc=nc; self.hc=hc; self.pc=pc; self.hover=False; self.down=False

    def draw(self, surf, font):
        c=self.pc if self.down else(self.hc if self.hover else self.nc)
        pygame.draw.rect(surf,c,self.rect,border_radius=6)
        pygame.draw.rect(surf,CG,self.rect,1,border_radius=6)
        t=font.render(self.text,True,CW); surf.blit(t,t.get_rect(center=self.rect.center))

    def handle(self, ev):
        if ev.type==pygame.MOUSEMOTION: self.hover=self.rect.collidepoint(ev.pos)
        if ev.type==pygame.MOUSEBUTTONDOWN and self.rect.collidepoint(ev.pos): self.down=True
        if ev.type==pygame.MOUSEBUTTONUP:
            c=self.down and self.rect.collidepoint(ev.pos); self.down=False; return c
        return False


# ======== FloatingPanel ========
class FloatingPanel:
    TITLE_H=24; MIN_W=300; MIN_H=260; RESIZE_Z=16

    def __init__(self, x, y, w, h, title):
        self.rect=pygame.Rect(x,y,w,h); self.collapsed=False; self.title=title
        self._drag=False; self._resize=False; self._drag_off=(0,0)
        self._prev_size=(w,h); self.surf=None; self._click_pos=None

    @property
    def visible_rect(self):
        if self.collapsed: return pygame.Rect(self.rect.x,self.rect.y,self.rect.w,self.TITLE_H)
        return self.rect

    def size_changed(self):
        s=(self.rect.w,self.rect.h)
        ch=s!=self._prev_size
        if ch: self._prev_size=s
        return ch

    def draw(self, screen, font_sm, font_xs):
        r=self.visible_rect
        pygame.draw.rect(screen,(18,30,60),(r.x,r.y,r.w,self.TITLE_H))
        pygame.draw.rect(screen,(55,80,140),(r.x,r.y,r.w,self.TITLE_H),1)
        arrow="[>] " if self.collapsed else "[v] "
        lbl=font_sm.render(arrow+self.title,True,(160,200,255))
        screen.blit(lbl,(r.x+8,r.y+5))
        hint=font_xs.render("drag|resize>>",True,(55,75,115))
        screen.blit(hint,(r.x+r.w-hint.get_width()-6,r.y+7))
        if not self.collapsed:
            pygame.draw.rect(screen,(8,12,24),(r.x,r.y+self.TITLE_H,r.w,r.h-self.TITLE_H))
            pygame.draw.rect(screen,(35,55,100),(r.x,r.y,r.w,r.h),1)
            if self.surf:
                body_h=max(1,r.h-self.TITLE_H)
                try: s2=pygame.transform.smoothscale(self.surf,(r.w,body_h))
                except: s2=self.surf
                screen.blit(s2,(r.x,r.y+self.TITLE_H))
            pts=[(r.right-self.RESIZE_Z,r.bottom),(r.right,r.bottom),(r.right,r.bottom-self.RESIZE_Z)]
            pygame.draw.polygon(screen,(70,100,160),pts)

    def handle(self, ev):
        r=self.visible_rect
        title_r=pygame.Rect(r.x,r.y,r.w,self.TITLE_H)
        resize_r=pygame.Rect(r.right-self.RESIZE_Z,r.bottom-self.RESIZE_Z,self.RESIZE_Z,self.RESIZE_Z)
        if ev.type==pygame.MOUSEBUTTONDOWN and ev.button==1:
            if not self.collapsed and resize_r.collidepoint(ev.pos):
                self._resize=True; return True
            if title_r.collidepoint(ev.pos):
                self._drag=True; self._click_pos=ev.pos
                self._drag_off=(ev.pos[0]-r.x,ev.pos[1]-r.y); return True
        if ev.type==pygame.MOUSEBUTTONUP and ev.button==1:
            if self._drag and self._click_pos:
                moved=abs(ev.pos[0]-self._click_pos[0])+abs(ev.pos[1]-self._click_pos[1])
                if moved<5: self.collapsed=not self.collapsed
            self._drag=False; self._resize=False; self._click_pos=None
        if ev.type==pygame.MOUSEMOTION:
            if self._resize:
                self.rect.w=max(self.MIN_W,ev.pos[0]-self.rect.x)
                self.rect.h=max(self.MIN_H,ev.pos[1]-self.rect.y); return True
            if self._drag:
                self.rect.x=max(0,min(W-self.rect.w,ev.pos[0]-self._drag_off[0]))
                self.rect.y=max(0,min(H-self.TITLE_H,ev.pos[1]-self._drag_off[1])); return True
        return False


# ======== Particle ========
class Particle:
    def __init__(self, ox, oz):
        spd=random.uniform(8,45); az=random.uniform(0,2*math.pi); el=random.uniform(0.1,math.pi/1.8)
        self.x=ox+random.uniform(-2,2); self.y=random.uniform(0,4); self.z=oz+random.uniform(-2,2)
        self.vx=math.cos(el)*math.cos(az)*spd; self.vy=math.sin(el)*spd+random.uniform(3,9)
        self.vz=math.cos(el)*math.sin(az)*spd
        self.life=random.uniform(0.5,1.6); self.t=0.0; self.alive=True
        self.size=random.uniform(2,7); self._drag_c=random.uniform(0.6,1.3)
        self._r0=random.choice([(255,140,30),(255,80,20),(200,50,10),(240,200,50),(255,60,0)])

    def update(self, dt):
        if not self.alive: return
        self.t+=dt
        if self.t>=self.life: self.alive=False; return
        self.vx*=(1-self._drag_c*dt); self.vz*=(1-self._drag_c*dt); self.vy-=G*dt*0.6
        self.x+=self.vx*dt; self.y+=self.vy*dt; self.z+=self.vz*dt
        if self.y<0: self.y=0; self.vy*=-0.12

    @property
    def alpha(self): return int(255*max(0,1-self.t/self.life))

    @property
    def color(self):
        f=min(1.0,self.t/self.life); r,g,b=self._r0
        return (int(r*(1-f)+40*f),int(g*(1-f)+20*f),int(b*(1-f)+10*f))


# ======== ImpactEffect ========
class ImpactEffect:
    def __init__(self, x, z, result):
        self.x=x; self.z=z; self.particles=[Particle(x,z) for _ in range(72)]
        self.sw_r=0.1; self.sw_alpha=230.0
        self.crater_r={"HIT":9,"NEAR":16,"MISS":26}.get(result,12)
        self.crater_col=(38,22,12); self._active=True

    def update(self, dt):
        for p in self.particles: p.update(dt)
        self.particles=[p for p in self.particles if p.alive]
        self.sw_r+=180*dt; self.sw_alpha-=460*dt
        if self.sw_alpha<=0 and not self.particles: self._active=False

    def draw(self, surf, cam):
        disk3(surf,cam,self.x,0.15,self.z,self.crater_r,self.crater_col,alpha=155,pool_idx=0)
        if self.sw_alpha>0:
            ring3(surf,cam,self.x,0.3,self.z,self.sw_r,(240,180,50),w=3)
        for p in self.particles:
            pp=cam.project(p.x,p.y,p.z)
            if pp:
                sc=max(1,int(p.size*150/max(pp[2],1)))
                try:
                    ps=pygame.Surface((sc*2+2,sc*2+2),pygame.SRCALPHA)
                    pygame.draw.circle(ps,(*p.color,p.alpha),(sc+1,sc+1),sc)
                    surf.blit(ps,(pp[0]-sc,pp[1]-sc))
                except: pass


# ======== Shell ========
class Shell:
    PN_GAIN=4.5; MAX_ACC=40.0; MAX_CANARD=15.0

    def __init__(self, pos, vel, wind, drag_coef, guid_on):
        self.pos=np.array(pos,dtype=float); self.vel=np.array(vel,dtype=float)
        self.wind=np.array(wind,dtype=float); self.drag=drag_coef; self.guid=guid_on
        self._spawn_pos=self.pos.copy()
        self._u_pos=self.pos.copy(); self._u_vel=self.vel.copy()
        self.trail=deque(maxlen=TRAIL_MAXLEN); self.u_trail=deque(maxlen=TRAIL_MAXLEN)
        self.trail.append(self.pos.copy()); self.u_trail.append(self.pos.copy())
        self.alive=True; self.result=None; self.land=None; self.t=0.0
        self.max_alt=self.pos[1]; self.impact_vel=0.0; self.total_dv_guid=0.0
        self.delta_pitch=0.0; self.delta_yaw=0.0; self.guided_phase="LAUNCH"
        self.hist={"t":[],"x":[],"y":[],"z":[],"dp":[],"dyw":[]}

    def _guidance_acc(self, target):
        tgt=np.array(target,dtype=float); los=tgt-self.pos
        dist=np.linalg.norm(los)
        if dist<3.0: return np.zeros(3)
        spd=np.linalg.norm(self.vel)
        if spd<1.0: return np.zeros(3)
        uv=self.vel/spd; los_u=los/dist
        cross1=np.cross(uv,los_u); acc=self.PN_GAIN*spd*np.cross(cross1,uv)
        amag=np.linalg.norm(acc)
        if amag>self.MAX_ACC: acc=acc*self.MAX_ACC/amag
        return acc

    def _canard_deg(self, acc):
        spd=max(np.linalg.norm(self.vel),1.0)
        pitch=max(-self.MAX_CANARD,min(self.MAX_CANARD,math.degrees(math.atan2(acc[1],spd))))
        yaw  =max(-self.MAX_CANARD,min(self.MAX_CANARD,math.degrees(math.atan2(acc[0],spd))))
        return pitch, yaw

    def _phase(self, target):
        los2=math.sqrt((self.pos[0]-target[0])**2+(self.pos[2]-target[2])**2)
        sp2 =math.sqrt((self._spawn_pos[0]-target[0])**2+(self._spawn_pos[2]-target[2])**2)
        if self.t<2.0: return "< LAUNCH"
        if abs(self.vel[1])<6.0: return "* APEX"
        if self.vel[1]>0: return "^ ASCENDING"
        if sp2>1 and los2<0.22*sp2: return "v TERMINAL"
        return "v DESCENDING"

    def step(self, dt, target):
        if not self.alive: return
        self.t+=dt
        acc=np.zeros(3)
        armed=np.linalg.norm(self.pos-self._spawn_pos)>ARMING_DIST
        if self.guid and armed: acc=self._guidance_acc(target)
        self.delta_pitch,self.delta_yaw=self._canard_deg(acc)
        self.total_dv_guid+=np.linalg.norm(acc)*dt
        self.vel+=acc*dt; self.vel[1]-=G*dt; self.vel+=self.wind*WIND_SCALE*dt
        spd=np.linalg.norm(self.vel)
        if spd>0 and self.drag>0:
            self.vel-=(self.vel/spd)*self.drag*spd*spd*dt
        self.pos+=self.vel*dt; self.trail.append(self.pos.copy())
        if self.pos[1]>self.max_alt: self.max_alt=self.pos[1]
        self._u_vel[1]-=G*dt; self._u_vel+=self.wind*WIND_SCALE*dt
        usp=np.linalg.norm(self._u_vel)
        if usp>0 and self.drag>0: self._u_vel-=(self._u_vel/usp)*self.drag*usp*usp*dt
        self._u_pos+=self._u_vel*dt; self.u_trail.append(self._u_pos.copy())
        if len(self.hist["t"])<2000:
            self.hist["t"].append(self.t); self.hist["x"].append(self.pos[0])
            self.hist["y"].append(self.pos[1]); self.hist["z"].append(self.pos[2])
            self.hist["dp"].append(self.delta_pitch); self.hist["dyw"].append(self.delta_yaw)
        self.guided_phase=self._phase(target)
        if self.pos[1]<=0:
            prev_y=self.pos[1]-self.vel[1]*dt
            if prev_y>0 and abs(self.vel[1])>1e-4:
                frac=prev_y/(prev_y-self.pos[1])
                land=self.pos-self.vel*dt*(1-frac); land[1]=0.0; self.land=land
            else:
                land=self.pos.copy(); land[1]=0.0; self.land=land
            self.pos[1]=0.0; self.alive=False; self.impact_vel=np.linalg.norm(self.vel)


# ======== 3D helpers ========
def p3(cam,x,y,z): return cam.project(x,y,z)

def ln3(surf,cam,a,b,col,w=1):
    pa=p3(cam,*a); pb=p3(cam,*b)
    if pa and pb: pygame.draw.line(surf,col,pa[:2],pb[:2],w)

def sph3(surf,cam,x,y,z,r,col,ol=False):
    p=p3(cam,x,y,z)
    if not p: return
    sr=max(3,int(r*240/p[2])); pygame.draw.circle(surf,col,p[:2],sr)
    if ol: pygame.draw.circle(surf,CW,p[:2],max(1,sr-1),1)

def disk3(surf,cam,cx,y,cz,r,col,alpha=28,segs=48,pool_idx=1):
    global _DISK_SURFS
    if _DISK_SURFS is None:
        _DISK_SURFS=[pygame.Surface((VIEW_W,VIEW_H),pygame.SRCALPHA) for _ in range(6)]
    s=_DISK_SURFS[pool_idx%len(_DISK_SURFS)]; s.fill((0,0,0,0))
    pts=[]
    for i in range(segs):
        a2=2*math.pi*i/segs; pp=p3(cam,cx+r*math.cos(a2),y,cz+r*math.sin(a2))
        if pp: pts.append(pp[:2])
    if len(pts)>2:
        pygame.draw.polygon(s,(*col,alpha),pts); surf.blit(s,(PANEL_W,0))

def ring3(surf,cam,cx,y,cz,r,col,w=2,segs=48):
    pts=[]
    for i in range(segs):
        a2=2*math.pi*i/segs; pp=p3(cam,cx+r*math.cos(a2),y,cz+r*math.sin(a2))
        if pp: pts.append(pp[:2])
    if len(pts)>2: pygame.draw.polygon(surf,col,pts,w)

def draw_sky(surf):
    global SKY_SURF
    if SKY_SURF is None:
        SKY_SURF=pygame.Surface((VIEW_W,VIEW_H))
        for y in range(VIEW_H):
            t=y/VIEW_H; c=tuple(int(CST[i]+(CSB[i]-CST[i])*t) for i in range(3))
            pygame.draw.line(SKY_SURF,c,(0,y),(VIEW_W,y))
    surf.blit(SKY_SURF,(PANEL_W,0))

def draw_ground(surf,cam):
    corners=[(-4000,0,-300),(4000,0,-300),(4000,0,9000),(-4000,0,9000)]
    pts=[p3(cam,x,y,z) for x,y,z in corners]; pts=[p[:2] for p in pts if p]
    if len(pts)==4: pygame.draw.polygon(surf,CGD,pts)

def draw_grid(surf,cam,size=5000,step=500):
    for i in range(-size,size+1,step):
        ln3(surf,cam,(i,0,-200),(i,0,size),(32,62,32))
        ln3(surf,cam,(-size,0,i),(size,0,i),(32,62,32))

def draw_shell_body(surf,cam,px,py,pz,vel_vec):
    spd=np.linalg.norm(vel_vec)
    if spd<0.1: return
    ax=vel_vec/spd; up=np.array([0.0,1.0,0.0])
    right=np.cross(ax,up); rn=np.linalg.norm(right)
    if rn<1e-4: right=np.array([1.0,0.0,0.0])
    else: right=right/rn
    up2=np.cross(right,ax)
    def pt(along,rad,ang):
        w=px+ax[0]*along+right[0]*rad*math.cos(ang)+up2[0]*rad*math.sin(ang)
        h=py+ax[1]*along+right[1]*rad*math.cos(ang)+up2[1]*rad*math.sin(ang)
        d=pz+ax[2]*along+right[2]*rad*math.cos(ang)+up2[2]*rad*math.sin(ang)
        return p3(cam,w,h,d)
    def stripe(a1,a2,r,col,segs=8):
        top=[]; bot=[]
        for i in range(segs+1):
            ang=2*math.pi*i/segs; t=pt(a1,r,ang); b=pt(a2,r,ang)
            if t: top.append(t[:2])
            if b: bot.append(b[:2])
        if len(top)>2 and len(bot)>2:
            poly=top+bot[::-1]; pygame.draw.polygon(surf,col,poly)
            pygame.draw.polygon(surf,CDK,poly,1)
    L=0.9; R=0.0775
    stripe(-L*0.50,-L*0.36,R*0.68,C_DARK); stripe(-L*0.36,+L*0.28,R,C_STEEL)
    stripe(-L*0.22,-L*0.10,R*1.05,C_COPPER); stripe(-L*0.10,-L*0.04,R*1.03,C_COPPER)
    stripe(+L*0.28,+L*0.37,R,C_GOLD); stripe(+L*0.37,+L*0.46,R*0.90,C_DARK)
    stripe(+L*0.39,+L*0.44,R*0.92,C_PCB)
    for fa in [0,math.pi/2,math.pi,3*math.pi/2]:
        f1=pt(+L*0.38,R*0.90,fa); f2=pt(+L*0.46,R*0.90,fa)
        f3=pt(+L*0.46,R*2.00,fa); f4=pt(+L*0.38,R*1.60,fa)
        fpts=[p[:2] for p in [f1,f2,f3,f4] if p]
        if len(fpts)==4:
            pygame.draw.polygon(surf,C_DARK,fpts); pygame.draw.polygon(surf,C_STEEL,fpts,1)
    stripe(+L*0.46,+L*0.58,R*0.88,C_NOSE); stripe(+L*0.58,+L*0.68,R*0.62,C_NOSE)
    stripe(+L*0.68,+L*0.76,R*0.38,C_NOSE); stripe(+L*0.76,+L*0.80,R*0.16,C_NOSE)
    tip=pt(+L*0.81,0,0)
    if tip:
        sr=max(2,int(4*200/max(tip[2],1))); pygame.draw.circle(surf,CCY,tip[:2],sr)

def draw_gun(surf,cam,angle_deg):
    rad=math.radians(angle_deg); tip=(0,8+math.sin(rad)*55,math.cos(rad)*55)
    for dx,dz in [(-10,-8),(10,-8),(10,8),(-10,8)]: ln3(surf,cam,(dx,0,dz),(dx,8,dz),(100,105,115),2)
    corners=[(dx,8,dz) for dx,dz in [(-10,-8),(10,-8),(10,8),(-10,8),(-10,-8)]]
    pts=[p3(cam,*c)[:2] for c in corners if p3(cam,*c)]
    if len(pts)>3: pygame.draw.polygon(surf,CDK,pts)
    ln3(surf,cam,(0,8,0),tip,(120,125,135),7); ln3(surf,cam,(0,8,0),tip,CW,2)
    sph3(surf,cam,*tip,3,COR,ol=True)

def draw_target(surf,cam,tx,tz,R,r,fxs):
    disk3(surf,cam,tx,0.2,tz,R,CR,alpha=22,pool_idx=2)
    disk3(surf,cam,tx,0.2,tz,r,CY,alpha=55,pool_idx=3)
    ring3(surf,cam,tx,0.3,tz,R,CR,w=2); ring3(surf,cam,tx,0.3,tz,r,CY,w=2)
    for h in range(0,45,6):
        c=CR if(h//6)%2==0 else CW; ln3(surf,cam,(tx,h,tz),(tx,h+6,tz),c,3)
    sph3(surf,cam,tx,47,tz,5,CR,ol=True)
    pR=p3(cam,tx+R,2,tz); pr=p3(cam,tx+r,2,tz)
    if pR: surf.blit(fxs.render("R",True,CR),(pR[0]+3,pR[1]-5))
    if pr: surf.blit(fxs.render("r",True,CY),(pr[0]+3,pr[1]-5))

def draw_trail(surf,cam,trail,col_base,dashed=False):
    pts_all=list(trail); n=len(pts_all)
    if n<2: return
    pts_sub=pts_all[::TRAIL_DRAW_STEP]; n2=len(pts_sub)
    for i in range(1,n2):
        if dashed and i%2==0: continue
        a=max(0.1,i/n2); c=tuple(min(255,int(ch*a)) for ch in col_base)
        pa=p3(cam,*pts_sub[i-1]); pb=p3(cam,*pts_sub[i])
        if pa and pb: pygame.draw.line(surf,c,pa[:2],pb[:2],max(1,int(a*2.5)))

def _compute_arc(angle,speed,wind,drag,tx,tz,cam):
    rad=math.radians(angle); az=math.atan2(tx,tz)
    vx=math.sin(az)*math.cos(rad)*speed; vy=math.sin(rad)*speed
    vz=math.cos(az)*math.cos(rad)*speed; wx,_,wz=wind
    pts=[]; land=None; px,py,pz=0.0,8.0,0.0
    for _ in range(ARC_STEPS):
        vy-=G*ARC_DT; vx+=wx*WIND_SCALE*ARC_DT; vz+=wz*WIND_SCALE*ARC_DT
        sp=math.sqrt(vx*vx+vy*vy+vz*vz)
        if sp>0 and drag>0:
            da=drag*sp*sp; vx-=(vx/sp)*da*ARC_DT; vy-=(vy/sp)*da*ARC_DT; vz-=(vz/sp)*da*ARC_DT
        px+=vx*ARC_DT; py+=vy*ARC_DT; pz+=vz*ARC_DT
        if py<0: land=(px,0,pz); break
        pp=cam.project(px,py,pz)
        if pp: pts.append(pp[:2])
    return pts, land

def draw_guide_arc(surf,cam,angle,speed,wind,drag,tx,tz,fxs):
    global _arc_cache
    key=(round(angle,1),round(speed),round(tx),round(tz),
         round(wind[0],1),round(wind[2],1),round(drag,4),
         round(cam.yaw,1),round(cam.pitch,1),round(cam.dist))
    if key!=_arc_cache["params"]:
        pts,land=_compute_arc(angle,speed,wind,drag,tx,tz,cam)
        _arc_cache["pts_u"]=pts; _arc_cache["land_u"]=land; _arc_cache["params"]=key
    for i in range(1,len(_arc_cache["pts_u"])):
        if i%2==0: continue
        pygame.draw.line(surf,(40,70,140),_arc_cache["pts_u"][i-1],_arc_cache["pts_u"][i],1)
    land=_arc_cache["land_u"]
    if land:
        pp=cam.project(*land)
        if pp:
            pygame.draw.circle(surf,CR,pp[:2],5)



# ======== build_graphs ========
def build_graphs(shells,target,gw,gh):
    tx,_,tz=target
    fig,axes=plt.subplots(2,2,figsize=(max(1,gw)/100,max(1,gh)/100),dpi=100)
    fig.patch.set_facecolor("#0A0E1C")
    plt.subplots_adjust(left=0.16,right=0.97,top=0.93,bottom=0.11,hspace=0.55,wspace=0.52)
    def sty(ax,title,xl,yl):
        ax.set_facecolor("#0D1220"); ax.set_title(title,color="#C0C8E0",fontsize=9,pad=3)
        ax.set_xlabel(xl,color="#8084A0",fontsize=8); ax.set_ylabel(yl,color="#8084A0",fontsize=8)
        ax.tick_params(colors="#606880",labelsize=7)
        for sp in ax.spines.values(): sp.set_color("#263050")
        ax.grid(True,color="#1A2040",linewidth=0.5)
    
    def d_leg(ax, loc="best"):
        h, l = ax.get_legend_handles_labels()
        d = {}
        for hn, ln in zip(h, l):
            if ln not in d and not ln.startswith("_"): d[ln] = hn
        if d: ax.legend(d.values(), d.keys(), fontsize=6, facecolor="#0D1220", labelcolor="white", loc=loc)

    ax0,ax1,ax2,ax3=axes[0,0],axes[0,1],axes[1,0],axes[1,1]
    
    sty(ax0,"Top-Down Trajectory","Downrange Z (m)","Lateral X (m)")
    ax0.plot(tz,tx,"b*",ms=8,label="Target",zorder=5)
    for sh in shells:
        if len(sh.trail)>2:
            ax0.plot([p[2] for p in sh.trail],[p[0] for p in sh.trail],color="#32C864",lw=2.0,label="Guided")
            ax0.plot([p[2] for p in sh.u_trail],[p[0] for p in sh.u_trail],color="#DC3737",lw=1.4,ls="--",label="Unguided")
    d_leg(ax0, loc="upper left")
    
    sty(ax1,"Altitude Profile","Downrange Z (m)","Altitude Y (m)")
    for sh in shells:
        if len(sh.trail)>2:
            ax1.plot([p[2] for p in sh.trail],[p[1] for p in sh.trail],color="#32C864",lw=2.0,label="Guided")
            ax1.plot([p[2] for p in sh.u_trail],[p[1] for p in sh.u_trail],color="#DC3737",lw=1.4,ls="--",label="Unguided")
            ys=[p[1] for p in sh.trail]; zs=[p[2] for p in sh.trail]
            if ys:
                mi=int(np.argmax(ys))
                try: ax1.annotate(f"peak:{ys[mi]:.0f}m",xy=(zs[mi],ys[mi]),fontsize=6,color="#FFD700",xytext=(zs[mi]+50,ys[mi]*0.85),arrowprops=dict(arrowstyle="->",color="#FFD700",lw=0.7))
                except: pass
    ax1.plot(tz,0,"bx",ms=7,label="Target"); ax1.axhline(0,color="#263050",lw=0.7)
    d_leg(ax1, loc="upper left")
    
    sty(ax2,"Lateral Correction","Downrange Z (m)","Deviation X (m)")
    for sh in shells:
        if len(sh.trail)>2:
            gd=[p[0]-tx for p in sh.trail]; ud=[p[0]-tx for p in sh.u_trail]
            ax2.plot([p[2] for p in sh.trail],gd,color="#32C864",lw=2.0,label="Guided")
            ax2.plot([p[2] for p in sh.u_trail],ud,color="#DC3737",lw=1.4,ls="--",label="Unguided")
            if sh.land is not None:
                mc="go" if sh.result=="HIT" else("y^" if sh.result=="NEAR" else "rx")
                ax2.plot(sh.land[2],sh.land[0]-tx,mc,ms=7,label=f"Result: {sh.result}")
    ax2.axhline(0,color="#3050A0",lw=1.2,ls=":",label="Target Center")
    d_leg(ax2, loc="best")
    
    sty(ax3,"Canard Commands","Time of Flight (s)","Canard Angle (deg)")
    for sh in shells:
        if len(sh.hist["t"])>2:
            ax3.plot(sh.hist["t"],sh.hist["dp"],color="#8060E0",lw=2.0,label="Pitch")
            ax3.plot(sh.hist["t"],sh.hist["dyw"],color="#30C8E0",lw=2.0,label="Yaw")
    ax3.axhline(15,color="#505050",lw=0.8,ls="--",label="Limit ±15°")
    ax3.axhline(-15,color="#505050",lw=0.8,ls="--")
    ax3.set_ylim(-20,20); d_leg(ax3, loc="best")
    
    buf=BytesIO(); fig.savefig(buf,format="png",dpi=100); buf.seek(0); plt.close(fig)
    return pygame.image.load(buf,"g.png")


# ======== MiniMap ========
class MiniMap:
    SIZE=165; ALPHA=195; MARGIN=8
    def __init__(self): self.surf=pygame.Surface((self.SIZE,self.SIZE),pygame.SRCALPHA)
    def draw(self,screen,shells,tx,tz,cam):
        ox=PANEL_W+self.MARGIN; oy=VIEW_H-self.SIZE-self.MARGIN
        self.surf.fill((10,15,30,self.ALPHA))
        max_range=max(math.sqrt(tx*tx+tz*tz)*1.3,500)
        s=(self.SIZE/2-10)/max_range; cx=self.SIZE//2; cy=self.SIZE//2
        for rv in [0.25,0.5,0.75,1.0]:
            pygame.draw.circle(self.surf,(30,50,80,120),(cx,cy),int(rv*(self.SIZE/2-4)),1)
        def w2m(x,z):
            mx=int(cx+x*s); my=int(cy-z*s)
            return (max(2,min(self.SIZE-3,mx)),max(2,min(self.SIZE-3,my)))
        tmx,tmy=w2m(tx,tz)
        pygame.draw.line(self.surf,(*CR,255),(tmx-6,tmy),(tmx+6,tmy),2)
        pygame.draw.line(self.surf,(*CR,255),(tmx,tmy-6),(tmx,tmy+6),2)
        for sh in shells:
            if sh.alive:
                pmx,pmy=w2m(sh.pos[0],sh.pos[2]); pygame.draw.circle(self.surf,(*CG,220),(pmx,pmy),3)
                umx,umy=w2m(sh._u_pos[0],sh._u_pos[2]); pygame.draw.circle(self.surf,(*CR,160),(umx,umy),2)
            elif sh.land is not None and sh.result:
                col=CG if sh.result=="HIT" else(CY if sh.result=="NEAR" else CR)
                pmx,pmy=w2m(sh.land[0],sh.land[2]); pygame.draw.circle(self.surf,(*col,200),(pmx,pmy),3)
        gmx,gmy=w2m(0,0)
        pygame.draw.polygon(self.surf,(*CW,230),[(gmx,gmy-6),(gmx-4,gmy+4),(gmx+4,gmy+4)])
        pygame.draw.rect(self.surf,(50,80,140,200),(0,0,self.SIZE,self.SIZE),2)
        screen.blit(self.surf,(ox,oy))


# ======== SplashScreen ========
class SplashScreen:
    def __init__(self,screen,clock,fonts):
        self.screen=screen; self.clock=clock; self.fonts=fonts
        self.sweep=0.0; self.pulse=0.0; self.pulse_dir=1

    def run(self):
        f_title,f_sub,f_body,f_xs=self.fonts
        running=True
        while running:
            dt=self.clock.tick(FPS)/1000.0
            for ev in pygame.event.get():
                if ev.type==pygame.QUIT: pygame.quit(); sys.exit()
                if ev.type in(pygame.KEYDOWN,pygame.MOUSEBUTTONDOWN): running=False
            self.sweep=(self.sweep+dt*90)%360
            self.pulse+=dt*2.5*self.pulse_dir
            if self.pulse>1.0: self.pulse=1.0; self.pulse_dir=-1
            if self.pulse<0.0: self.pulse=0.0; self.pulse_dir=1
            surf=self.screen; surf.fill((6,9,20))
            cx,cy=W//2,H//2
            for ri,rr in enumerate([100,180,260,340,420]):
                a=50+ri*12; pygame.draw.circle(surf,(0,a,a//2),(cx,cy),rr,1)
            sr=math.radians(self.sweep)
            for i in range(42):
                ao=sr-math.radians(i*3.5); fade=int((1-i/42)*110)
                ex=int(cx+420*math.sin(ao)); ey=int(cy-420*math.cos(ao))
                pygame.draw.line(surf,(0,fade,fade//2),(cx,cy),(ex,ey),1)
            t1=f_title.render("155mm PGK GUIDANCE SIMULATOR",True,(220,230,255))
            surf.blit(t1,t1.get_rect(center=(cx,cy-115)))
            t2=f_sub.render("Team: Vector Victims",True,CCY)
            surf.blit(t2,t2.get_rect(center=(cx,cy-62)))
            t3=f_sub.render("Problem Statement: PS26098  |  SIH 2026",True,CY)
            surf.blit(t3,t3.get_rect(center=(cx,cy-24)))
            t4=f_body.render("Developed for: YIL / Ministry of Defence",True,CGR)
            surf.blit(t4,t4.get_rect(center=(cx,cy+16)))
            pygame.draw.line(surf,(35,55,100),(cx-330,cy+52),(cx+330,cy+52),1)
            a2=int(120+135*self.pulse)
            ps=f_sub.render("  >>  PRESS ANY KEY TO BEGIN  <<  ",True,(a2,a2,a2))
            surf.blit(ps,ps.get_rect(center=(cx,cy+105)))
            strip=pygame.Surface((W,28),pygame.SRCALPHA); strip.fill((10,18,48,175))
            surf.blit(strip,(0,H-28))
            bot=f_xs.render("SIH 2026  |  PS26098  |  Team: Vector Victims  |  YIL / Ministry of Defence",True,(75,95,140))
            surf.blit(bot,bot.get_rect(center=(cx,H-14)))
            pygame.display.flip()


# ======== App ========
class App:
    def __init__(self):
        pygame.init()
        self.screen=pygame.display.set_mode((W,H))
        pygame.display.set_caption("155mm PGK SIM v4 | Vector Victims | PS26098")
        self.clock=pygame.time.Clock()
        self.f_lg =pygame.font.SysFont("consolas",17,bold=True)
        self.f_md =pygame.font.SysFont("consolas",13,bold=True)
        self.f_sm =pygame.font.SysFont("consolas",12)
        self.f_xs =pygame.font.SysFont("consolas",10)
        self.f_ttl=pygame.font.SysFont("consolas",26,bold=True)
        SplashScreen(self.screen,self.clock,(self.f_ttl,self.f_lg,self.f_md,self.f_xs)).run()
        self.audio=AudioSystem()
        self.cam=Camera(); self.shells=[]; self.impacts=[]
        self.stats={"fired":0,"hits":0,"near":0}
        self.auto=False; self.auto_t=0.0; self.guid_on=True
        self.graph_lock=threading.Lock(); self.graph_pending=None
        self.graph_thread=None; self.graph_surf=None; self.g_timer=0.0
        self.float_panel=FloatingPanel(GRAPH_X,0,GRAPH_W_DEFAULT,GRAPH_H_DEFAULT,"Live Telemetry Graphs")
        self.minimap=MiniMap(); self.show_minimap=True
        self._fullscreen=False; self._rdrag=False; self._rlast=None
        self._build_ui()

    def _build_ui(self):
        px=10; gap=52; y0=50; pw=PANEL_W-20
        self.sl={
            "R":   Slider(px,y0+gap*0,pw,"R  target zone", 30,500,150,"m",  col=CR),
            "r":   Slider(px,y0+gap*1,pw,"r  CEP (hit)",   10,100, 30,"m",  col=CY),
            "tx":  Slider(px,y0+gap*2,pw,"Target X",    -1500,1500,300,"m", col=CPK),
            "tz":  Slider(px,y0+gap*3,pw,"Target Z (d)", 300,5000,900,"m",  col=CCY),
            "ang": Slider(px,y0+gap*4,pw,"Launch angle",  20, 75, 45,"deg", col=(100,220,100)),
            "spd": Slider(px,y0+gap*5,pw,"Muzzle speed", 100,900,300,"m/s", col=COR),
            "wx":  Slider(px,y0+gap*6,pw,"Wind X",       -25, 25,  0,"m/s",fmt=".1f",col=CPK),
            "wz":  Slider(px,y0+gap*7,pw,"Wind Z",       -25, 25,  5,"m/s",fmt=".1f",col=CPK),
            "drag":Slider(px,y0+gap*8,pw,"Drag Cd",    0.000,0.008,0.001,"",fmt=".4f",col=CGR),
            "vol": Slider(px,y0+gap*9,pw,"Volume",       0.0, 1.0, 0.7,"",  fmt=".2f",col=CCY),
        }
        bw=118; bx=PANEL_W//2-59; yb=y0+gap*10+6
        self.btn_fire =Button(bx,yb,    bw,36,">> FIRE")
        self.btn_auto =Button(bx,yb+43, bw,28,"A  AUTO", nc=(30,60,120),hc=(45,90,160))
        self.btn_guid =Button(bx,yb+78, bw,28,"G GUID ON",nc=(25,90,30),hc=(35,130,45))
        self.btn_clear=Button(bx,yb+114,bw,28,"C  CLEAR", nc=(70,25,25),hc=(110,35,35))

    def v(self,k): return self.sl[k].value
    def target(self): return (self.v("tx"),0.0,self.v("tz"))
    def wind(self): return [self.v("wx"),0.0,self.v("wz")]

    def fire(self,jitter=True):
        ang=self.v("ang"); spd=self.v("spd"); tx=self.v("tx"); tz=self.v("tz")
        az=math.atan2(tx,tz); rad=math.radians(ang)
        jit_az=0; jit_ang=0; jit_spd=1
        if jitter:
            jit_az=random.gauss(0,0.018); jit_ang=random.gauss(0,0.006); jit_spd=random.gauss(1,0.005)
        az2=az+jit_az; rad2=rad+jit_ang; spd2=spd*jit_spd
        vx=math.sin(az2)*math.cos(rad2)*spd2; vy=math.sin(rad2)*spd2
        vz=math.cos(az2)*math.cos(rad2)*spd2
        bl=55.0
        spawn=[math.sin(az)*math.cos(rad)*bl,8+math.sin(rad)*bl,math.cos(az)*math.cos(rad)*bl]
        sh=Shell(spawn,[vx,vy,vz],self.wind(),self.v("drag"),self.guid_on)
        self.shells.append(sh); self.stats["fired"]+=1; self.audio.play_fire()

    def check_land(self):
        tx,_,tz=self.target(); R=self.v("R"); r=self.v("r")
        for sh in self.shells:
            if not sh.alive and sh.result is None and sh.land is not None:
                dx=sh.land[0]-tx; dz=sh.land[2]-tz; d=math.sqrt(dx*dx+dz*dz)
                if d<=r: sh.result="HIT"; self.stats["hits"]+=1
                elif d<=R: sh.result="NEAR"; self.stats["near"]+=1
                else: sh.result="MISS"
                self.impacts.append(ImpactEffect(sh.land[0],sh.land[2],sh.result))
                self.audio.play_impact(); self.audio.stop_whistle()

    def handle_rdrag(self,ev):
        in_v=lambda p: PANEL_W<p[0]<PANEL_W+VIEW_W
        if ev.type==pygame.MOUSEBUTTONDOWN and ev.button==3 and in_v(ev.pos):
            self._rdrag=True; self._rlast=ev.pos
        if ev.type==pygame.MOUSEBUTTONUP and ev.button==3: self._rdrag=False
        if ev.type==pygame.MOUSEMOTION and self._rdrag and self._rlast:
            dx=ev.pos[0]-self._rlast[0]; dy=ev.pos[1]-self._rlast[1]
            ry=math.radians(self.cam.yaw); sc=self.cam.dist*0.009
            dtx=dx*math.cos(ry)*sc; dtz=-dy*sc
            self.sl["tx"].target_value=max(self.sl["tx"].vmin,min(self.sl["tx"].vmax,self.v("tx")+dtx))
            self.sl["tz"].target_value=max(self.sl["tz"].vmin,min(self.sl["tz"].vmax,self.v("tz")+dtz))
            self._rlast=ev.pos

    def draw_3d(self):
        draw_sky(self.screen); draw_ground(self.screen,self.cam); draw_grid(self.screen,self.cam)
        tx,_,tz=self.target()
        draw_guide_arc(self.screen,self.cam,self.v("ang"),self.v("spd"),self.wind(),self.v("drag"),tx,tz,self.f_xs)
        draw_target(self.screen,self.cam,tx,tz,self.v("R"),self.v("r"),self.f_xs)
        draw_gun(self.screen,self.cam,self.v("ang"))
        for eff in self.impacts: eff.draw(self.screen,self.cam)
        for sh in self.shells:
            draw_trail(self.screen,self.cam,sh.u_trail,(200,50,50),dashed=True)
            draw_trail(self.screen,self.cam,sh.trail,(50,160,255))
            if sh.alive:
                draw_shell_body(self.screen,self.cam,sh.pos[0],sh.pos[1],sh.pos[2],sh.vel)
                pp=p3(self.cam,sh.pos[0],sh.pos[1]+25,sh.pos[2])
                if pp:
                    correcting=(abs(sh.delta_yaw)>0.5 or abs(sh.delta_pitch)>0.5)
                    col=CCY if correcting else CGR
                    armed=np.linalg.norm(sh.pos-sh._spawn_pos)>ARMING_DIST
                    if not armed: txt="[ARMING...]"
                    elif correcting: txt=f"{sh.guided_phase} P:{sh.delta_pitch:+.1f} Y:{sh.delta_yaw:+.1f}"
                    else: txt=sh.guided_phase
                    ct=self.f_xs.render(txt,True,col)
                    bg=pygame.Surface((ct.get_width()+4,ct.get_height()+2),pygame.SRCALPHA)
                    bg.fill((0,0,0,145)); self.screen.blit(bg,(pp[0]-ct.get_width()//2-2,pp[1]-1))
                    self.screen.blit(ct,(pp[0]-ct.get_width()//2,pp[1]))
            elif sh.result and sh.land is not None:
                col=CG if sh.result=="HIT" else(CY if sh.result=="NEAR" else CR)
                lp=p3(self.cam,sh.land[0],1,sh.land[2])
                if lp:
                    miss=math.sqrt((sh.land[0]-tx)**2+(sh.land[2]-tz)**2)
                    mt=self.f_xs.render(f"{sh.result}  {miss:.1f}m off",True,col)
                    self.screen.blit(mt,(lp[0]+10,lp[1]-6))
        self._draw_hud(tx,tz)
        if self.show_minimap: self.minimap.draw(self.screen,self.shells,tx,tz,self.cam)

    def _draw_hud(self,tx,tz):
        fired=self.stats["fired"]; hits=self.stats["hits"]
        near=self.stats["near"]; miss2=fired-hits-near
        pct=hits/fired*100 if fired>0 else 0
        hcol=CG if pct>=70 else(CY if pct>=40 else CR)
        live_sh=None
        for sh in reversed(self.shells):
            if sh.alive: live_sh=sh; break
        if live_sh is None:
            for sh in reversed(self.shells):
                if sh.land is not None: live_sh=sh; break
        rows=[
            ("155mm PGK SIH2026",CW),("Team: Vector Victims",CCY),("",CW),
            (f"Fired  : {fired}",CW),(f"HIT    : {hits}",CG),(f"NEAR   : {near}",CY),
            (f"MISS   : {miss2}",CR),(f"CEP    : {pct:.1f}%",hcol),("",CW),
            (f"Tgt X  : {self.v('tx'):.0f}m",CPK),(f"Tgt Z  : {self.v('tz'):.0f}m",CCY),
            (f"Tgt Rng: {math.sqrt(self.v('tx')**2+self.v('tz')**2):.0f}m",CGR),
            (f"Guid   : {'ON' if self.guid_on else 'OFF'}",CG if self.guid_on else CR),
        ]
        if live_sh:
            rows.append(("",CW))
            if live_sh.alive:
                spd=np.linalg.norm(live_sh.vel)
                rng=math.sqrt((live_sh.pos[0]-tx)**2+(live_sh.pos[2]-tz)**2)
                rows+=[(f"Speed  : {spd:.0f} m/s",COR),(f"Alt    : {live_sh.pos[1]:.0f}m",CCY),
                       (f"TOF    : {live_sh.t:.1f}s",CW),(f"Rng2Tgt: {rng:.0f}m",CGR),
                       (f"MaxAlt : {live_sh.max_alt:.0f}m",CY)]
            else:
                rows+=[(f"ImpactV: {live_sh.impact_vel:.0f} m/s",COR),
                       (f"MaxAlt : {live_sh.max_alt:.0f}m",CY),(f"TOF    : {live_sh.t:.1f}s",CW),
                       (f"GuidDV : {live_sh.total_dv_guid:.0f}",CCY)]
        y=10
        for txt,col in rows:
            s=self.f_xs.render(txt,True,col); self.screen.blit(s,(PANEL_W+6,y)); y+=14
        hints=["LDrag=orbit(inertia)","Scroll=zoom","RDrag=move target",
               "R=cam  M=minimap  F11=fullscreen  G=guid"]
        yh=VIEW_H-len(hints)*13-4
        for h in hints:
            hs=self.f_xs.render(h,True,(95,115,165))
            self.screen.blit(hs,(PANEL_W+VIEW_W-hs.get_width()-5,yh)); yh+=13

    def draw_panel(self):
        pygame.draw.rect(self.screen,CP,(0,0,PANEL_W,H))
        pygame.draw.line(self.screen,CS,(PANEL_W-1,0),(PANEL_W-1,H),2)
        t=self.f_lg.render("155mm PGK SIM v4",True,CW)
        t2=self.f_xs.render("Vector Victims | PS26098 | YIL/MoD",True,CGR)
        self.screen.blit(t,t.get_rect(centerx=PANEL_W//2,y=8))
        self.screen.blit(t2,t2.get_rect(centerx=PANEL_W//2,y=26))
        pygame.draw.line(self.screen,CS,(6,42),(PANEL_W-6,42),1)
        for sl in self.sl.values(): sl.draw(self.screen,self.f_sm,self.f_xs)
        if self.guid_on: self.btn_guid.text="G GUID ON";  self.btn_guid.nc=(25,90,30)
        else:            self.btn_guid.text="G GUID OFF"; self.btn_guid.nc=(80,20,20)
        self.btn_fire.draw(self.screen,self.f_md); self.btn_auto.draw(self.screen,self.f_sm)
        self.btn_guid.draw(self.screen,self.f_sm); self.btn_clear.draw(self.screen,self.f_sm)
        if self.auto:
            at=self.f_sm.render("* AUTO FIRING",True,CR)
            self.screen.blit(at,at.get_rect(centerx=PANEL_W//2,y=self.btn_clear.rect.bottom+5))
        d=math.sqrt(self.v("tx")**2+self.v("tz")**2); vs=self.v("spd")
        arg=G*d/(vs*vs) if vs>0 else 2.0
        yh=self.btn_clear.rect.bottom+24
        if abs(arg)<=1.0:
            ia=math.degrees(0.5*math.asin(arg))
            it=self.f_xs.render(f"Ideal angle ~ {ia:.1f}deg",True,CG)
        else:
            it=self.f_xs.render("Target out of range!",True,CR)
        self.screen.blit(it,it.get_rect(centerx=PANEL_W//2,y=yh))
        if len(self.shells)>=MAX_SHELLS-3:
            wt=self.f_xs.render(f"WARN shells:{len(self.shells)}/{MAX_SHELLS}",True,CY)
            self.screen.blit(wt,wt.get_rect(centerx=PANEL_W//2,y=yh+14))
        pygame.draw.line(self.screen,CS,(6,H-15),(PANEL_W-6,H-15),1)
        v2=self.f_xs.render("SIH2026 | PS26098 | Vector Victims",True,(40,55,90))
        self.screen.blit(v2,v2.get_rect(centerx=PANEL_W//2,y=H-12))

    def _rebuild_graphs_bg(self,shells_snap,tgt):
        try:
            surf=build_graphs(shells_snap,tgt,self.float_panel.rect.w,
                              max(1,self.float_panel.rect.h-FloatingPanel.TITLE_H))
            with self.graph_lock: self.graph_pending=surf
        except: pass

    def _tick_graphs(self,tgt):
        if(self.g_timer>=GRAPH_REFRESH and
           (self.graph_thread is None or not self.graph_thread.is_alive()) and self.shells):
            self.g_timer=0.0; snap=list(self.shells)
            t=threading.Thread(target=self._rebuild_graphs_bg,args=(snap,tgt),daemon=True)
            self.graph_thread=t; t.start()
        with self.graph_lock:
            if self.graph_pending is not None:
                self.float_panel.surf=self.graph_pending; self.graph_pending=None
        if self.float_panel.size_changed() and self.shells: self.g_timer=GRAPH_REFRESH

    def run(self):
        while True:
            dt=min(self.clock.tick(FPS)/1000.0,0.05)
            tgt=self.target()
            for ev in pygame.event.get():
                if ev.type==pygame.QUIT: pygame.quit(); sys.exit()
                if ev.type==pygame.KEYDOWN:
                    if ev.key==pygame.K_ESCAPE: pygame.quit(); sys.exit()
                    if ev.key==pygame.K_SPACE: self.fire()
                    if ev.key==pygame.K_a: self.auto=not self.auto
                    if ev.key==pygame.K_r: self.cam.reset()
                    if ev.key==pygame.K_m: self.show_minimap=not self.show_minimap
                    if ev.key==pygame.K_g: self.guid_on=not self.guid_on
                    if ev.key==pygame.K_c:
                        self.shells.clear(); self.impacts.clear()
                        self.stats={"fired":0,"hits":0,"near":0}
                    if ev.key==pygame.K_F11:
                        self._fullscreen=not self._fullscreen
                        if self._fullscreen: self.screen=pygame.display.set_mode((W,H),pygame.FULLSCREEN)
                        else: self.screen=pygame.display.set_mode((W,H))
                fp_handled=self.float_panel.handle(ev)
                if not fp_handled:
                    self.cam.handle(ev); self.handle_rdrag(ev)
                    for sl in self.sl.values(): sl.handle(ev)
                    if self.btn_fire.handle(ev): self.fire()
                    if self.btn_auto.handle(ev): self.auto=not self.auto
                    if self.btn_guid.handle(ev): self.guid_on=not self.guid_on
                    if self.btn_clear.handle(ev):
                        self.shells.clear(); self.impacts.clear()
                        self.stats={"fired":0,"hits":0,"near":0}
            if self.auto:
                self.auto_t+=dt
                if self.auto_t>=1.8: self.auto_t=0; self.fire()
            self.cam.update()
            for sl in self.sl.values(): sl.update(dt)
            self.audio.set_volumes(self.v("vol"),0.85,0.4)
            for sh in self.shells: sh.step(dt,tgt)
            self.check_land()
            for eff in self.impacts: eff.update(dt)
            if len(self.shells)>MAX_SHELLS: self.shells=self.shells[-MAX_SHELLS:]
            self.g_timer+=dt; self._tick_graphs(tgt)
            self.screen.fill(CB)
            self.draw_3d(); self.draw_panel()
            self.float_panel.draw(self.screen,self.f_sm,self.f_xs)
            pygame.display.set_caption(
                f"155mm PGK v4 | Vector Victims | PS26098  FPS:{self.clock.get_fps():.0f}  Shells:{len(self.shells)}")
            pygame.display.flip()

if __name__=="__main__":
    App().run()
