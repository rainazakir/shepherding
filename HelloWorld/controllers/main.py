#!/usr/bin/env python3
# This is the main control loop running in each argos robot

# /* Import Packages */
#######################################################################
import random, math
import time, sys, os

import json
experimentFolder = os.environ['EXPERIMENTFOLDER']
sys.path += [os.environ['MAINFOLDER'], \
             os.environ['EXPERIMENTFOLDER']+'/controllers', \
             os.environ['EXPERIMENTFOLDER']
            ]

from controllers.movement import RandomWalk, Navigate, Odometry, OdoCompass, GPS
from controllers.groundsensor import ResourceVirtualSensor, Resource, GroundSensor
from controllers.erandb import ERANDB
from controllers.rgbleds import RGBLEDs
from controllers.aux import *
from controllers.aux import Timer
from controllers.statemachine import *

from controllers.control_params import params as cp


# /* Global Variables */
#######################################################################
global robot

global startFlag
global notdonemod
notdonemod=False
startFlag = False

global txList, tripList, submodules
txList, tripList, submodules = [], [], []

global clocks, counters, logs, txs
clocks, counters, logs, txs = dict(), dict(), dict(), dict()


global estimate, totalWhite, totalBlack, byzantine, byzantine_style, log_folder
TPS = 5000

estimate =0
totalWhite =0
totalBlack=0

byzantine = 0
# /* Logging Levels for Console and File */
#######################################################################
import logging
loglevel = 10
logtofile = False 

# /* Experiment Global Variables */
#######################################################################

clocks['peering'] = Timer(5)
clocks['sensing'] = Timer(2)
clocks['ubi'] = Timer(100)
clocks['block']   = Timer(120)
clocks['newround'] = Timer(20)
clocks['voting'] = Timer(30)
global geth_peer_count



####################################################################################################################################################################################
#### INIT STEP #####################################################################################################################################################################
####################################################################################################################################################################################

def init():
    global clocks,counters, logs, submodules, me, rw, nav, odo, gps, rb, w3, fsm, rs, erb, rgb,odo2,gs, byzantine_style, log_folder
    robotID = str(int(robot.variables.get_id()[2:])+1)
    robotIP = '127.0.0.1'
    robot.variables.set_attribute("id", str(robotID))
    robot.variables.set_attribute("byzantine_style", str(0))
    robot.variables.set_attribute("consensus_reached",str("false"))
    robot.variables.set_attribute("scresources", "[]")
    robot.variables.set_attribute("foraging", "")
    robot.variables.set_attribute("state", "")
    robot.variables.set_attribute("quantity", "0")
    robot.variables.set_attribute("block", "")
    robot.variables.set_attribute("block", "0")
    robot.variables.set_attribute("hash", str(hash("genesis")))
    robot.variables.set_attribute("state_hash", str(hash("genesis")))

    # /* Initialize Console Logging*/
    #######################################################################
    log_folder = experimentFolder + '/logs/' + robotID + '/'

    # Monitor logs (recorded to file)
    name =  'monitor.log'
    os.makedirs(os.path.dirname(log_folder+name), exist_ok=True) 
    logging.basicConfig(filename=log_folder+name, filemode='w+', format='[{} %(levelname)s %(name)s] %(message)s'.format(robotID))
    logging.getLogger('sc').setLevel(10)
    logging.getLogger('w3').setLevel(10)
    logging.getLogger('poa').setLevel(10)
    robot.log = logging.getLogger()
    robot.log.setLevel(0)

    # /* Initialize submodules */
    #######################################################################
    # # /* Init web3.py */
    robot.log.info('Initialising Python Geth Console...')
    #w3 = Node(robotID, robotIP, 1233 + int(robotID), ProofOfAuthority(GENESIS))

    # /* Init an instance of peer for this Pi-Puck */
    me = Peer(robotID, robotIP)

    # /* Init E-RANDB __listening process and transmit function
    robot.log.info('Initialising RandB board...')
    erb = ERANDB(robot, cp['erbDist'] , cp['erbtFreq'])

    #/* Init Resource-Sensors */
    robot.log.info('Initialising resource sensor...')
    rs = ResourceVirtualSensor(robot)
    
    # /* Init Random-Walk, __walking process */
    robot.log.info('Initialising random-walk...')
    #rw = RandomWalk(robot, cp['scout_speed'])

    # /* Init Navigation, __navigate process */
    robot.log.info('Initialising navigation...')
    nav = Navigate(robot, cp['recruit_speed'])
    
    # /* Init odometry sensor */
    robot.log.info('Initialising Odo...')
    odo2 = Odometry(robot)
    
    # /* Init odometry sensor */
    robot.log.info('Initialising odometry...')
    odo = OdoCompass(robot)

    # /* Init GPS sensor */
    robot.log.info('Initialising gps...')
    gps = GPS(robot)

    # /* Init LEDs */
    rgb = RGBLEDs(robot)

    # /* Init Finite-State-Machine */
    fsm = FiniteStateMachine(robot, start = States.IDLE)

    # /* Init Ground sensor */
    robot.log.info('Initialising Ground sensor...')
    gs = GroundSensor(robot)
    
    # List of submodules --> iterate .start() to start all
    submodules = [erb,gs]

#########################################################################################################################
#### CONTROL STEP #######################################################################################################
#########################################################################################################################
global pos
pos = [0,0]
global last
last = 0
counter = 0
global checkt

def calculate_distance(position1, position2):
    x1, y1 = position1
    x2, y2 = position2
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

def find_nearest_peer(robot_position, erb_peers):
    min_distance = float('inf')
    nearest_peer = None

    for peer in erb_peers:
        print(peer.x,peer.y)
        distance = calculate_distance(robot_position, (peer.x, peer.y))
        if distance < min_distance:
            min_distance = distance
            nearest_peer = peer

    return nearest_peer, min_distance

def compute_total_force_and_movement(robot, robot_position, erb_peers):
    total_F_x = 0
    total_F_y = 0
    K_ij = 5  # Assuming some constant value for K_ij
    L_ij = 10  # Assuming some constant value for L_ij (equilibrium distance)

    # Compute the total force from all peers
    for peer in erb_peers:
        delta_xx = peer.x - robot_position[0] 
        delta_yy = peer.y - robot_position[1]

        delta_x = float(delta_xx)
        delta_y = float(delta_yy)

        min_distance = calculate_distance(robot_position, (peer.x, peer.y))

        # Force contribution from this peer
        F_x = -K_ij / L_ij * (L_ij - min_distance) * delta_x / min_distance
        F_y = -K_ij / L_ij * (L_ij - min_distance) * delta_y / min_distance

        # Sum the forces from all peers
        total_F_x += F_x
        total_F_y += F_y

    # Calculate movement based on the total force
    orientation_angle = odo.getOrientation()  # Assuming this returns the orientation angle in degrees
    N_x = math.cos(orientation_angle)
    N_y = math.sin(orientation_angle)
    Nx_orthogonal = math.cos(orientation_angle + math.pi / 2)
    Ny_orthogonal = math.sin(orientation_angle + math.pi / 2)

    # Control parameters for movement
    alpha = 30
    beta = 100
    delta_position = alpha * (total_F_x * N_x + total_F_y * N_y)
    delta_angle = beta * (total_F_x * Nx_orthogonal + total_F_y * Ny_orthogonal)
    V_0 = 40 * 0.00013
    robot_width = 7

    # Calculate wheel speeds
    left_wheel_speed = ((V_0 + delta_position + robot_width * delta_angle / 2) * 1000 / 0.13)
    right_wheel_speed = ((V_0 + delta_position - robot_width * delta_angle / 2) * 1000 / 0.13)

    # Log and set wheel speeds
    print("Right speed: ", right_wheel_speed, "Left speed: ", left_wheel_speed)
    robot.epuck_wheels.set_speed(left_wheel_speed, right_wheel_speed)

def controlstep():
    global counter, last, pos, clocks, counters, startFlag, startTime, notdonemod, odo2, checkt, byzantine, byzantine_style, log_folder
    global estimate, totalWhite, totalBlack

    for clock in clocks.values():
        clock.time.step()
        checkt = clock.time.time_counter

    if not startFlag:
        startFlag = True
        startTime = 0
        robot.log.info('--//-- Starting Experiment --//--')
        for module in submodules:
            try:
                module.start()
            except:
                robot.log.critical('Error Starting Module: %s', module)
                sys.exit()
        for log in logs.values():
            log.start()
        for clock in clocks.values():
            clock.reset()
        byzantine_style = int(robot.variables.get_attribute("byzantine_style"))

    else:
        ###########################
        ######## ROUTINES #########
        ###########################

        def peering():
            global estimate, totalWhite, totalBlack, checkt, byzantine, byzantine_style, log_folder

            # Get the current peers from ERB
            erb_enodes = {peer.id for peer in erb.peers}

            # Turn on LEDs according to geth peer count
            rgb.setLED(rgb.all, rgb.presets.get(len(erb_enodes), 3*['red']))

        # Perform submodules step
        for module in [erb, rs, gs, odo]:
            module.step()

        # Perform clock steps
        for clock in clocks.values():
            clock.time.step()

        if clocks['peering'].query():
            peering()


        # Get the current position of the robot
        robot_position = gps.getPosition()
        print(me.id ,"Robots position: ", robot_position)
        print("Robots pers: ", erb.peers)
        # Find the nearest peer
        nearest_peer, min_distance = find_nearest_peer(robot_position, erb.peers)
        print("Nearest peer: ", nearest_peer," and distance: ", min_distance)
        
        if len(erb.peers) > 0:
            compute_total_force_and_movement(robot, robot_position, erb.peers)
        #if nearest_peer:
            # Compute the force and movement based on the nearest peer
           # compute_force_and_movement(robot, robot_position, nearest_peer, min_distance)
def reset():
    pass

def destroy():
        
    print('Killed robot '+ me.id)

#########################################################################################################################
#########################################################################################################################
#########################################################################################################################
