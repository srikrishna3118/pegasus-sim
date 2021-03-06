#!/usr/bin/env python

'''
Pegasus Controller. Manages the transformations between different frame of references.
Provides '/map' poses to other components.
Controls mavros.
All GPS coordinates are normalized to UTM.
'''

import numpy as np

import rospy
import tf2_geometry_msgs
import tf2_ros
from geodesy import utm
from geometry_msgs.msg import PoseStamped, TransformStamped, PointStamped
from mavros_msgs.msg import State as MavrosState
from mavros_msgs.srv import SetMode, CommandBool
from nav_msgs.msg import Path
from sensor_msgs.msg import NavSatFix
from std_msgs.msg import UInt8
from std_srvs.srv import Trigger, TriggerResponse
from tf.transformations import quaternion_from_euler

from pegasus_controller.agent import Agent
from pegasus_controller.state import State
from pegasus_controller.controller_thread import ControllerThread


class PegasusController(object):
    def __init__(self, params):
        self.params = params
        self.agents = []
        for a_id, agent in enumerate(params['agents']):
            self.agents.append(Agent(self, a_id, agent[0], agent[1], agent[2]))
        self.tf_broadcaster = tf2_ros.TransformBroadcaster()
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)
        self.global_map_name = 'pegasus_map'
        self.subscribers = {}
        self._subscribe_map_origin()
        self.services = {}
        self._create_services()
        self.state = State.IDLE

    def spin(self):
        rate = rospy.Rate(10)
        while not rospy.is_shutdown():
            for agent in self.agents:
                agent.spin()
            rate.sleep()

    def _subscribe_map_origin(self):
        topic = self.params['map_origin_topic']
        self.subscribers['map_origin'] = rospy.Subscriber(topic, PoseStamped, self._recv_map_origin)

    def _create_services(self):
        self.services['start_mission'] = rospy.Service('start_mission', Trigger, self._start_mission)
        self.services['abort_mission'] = rospy.Service('abort_mission', Trigger, self._abort_mission)

    def _start_mission(self, request):
        rospy.loginfo('Starting mission')
        if self.state == State.IDLE:
            self.state = State.PLAN
            return TriggerResponse(True, 'Mission started.')
        else:
            return TriggerResponse(False, 'Mission in progress.')

    def _abort_mission(self, request):
        rospy.loginfo('Aborting mission')
        if self.state not in (State.COMPLETE, State.IDLE):
            self.state = State.COMPLETE
            return TriggerResponse(True, 'Mission aborted.')
        else:
            return TriggerResponse(False, 'Mission not running.')

    def _recv_map_origin(self, data):
        map_origin_utm = utm.fromLatLong(
            data.pose.position.y,
            data.pose.position.x,
            data.pose.position.z)

        self.map_origin_point = np.array((
            map_origin_utm.toPoint().x,
            map_origin_utm.toPoint().y))

        self.subscribers['map_origin'].unregister()

        '''
        self.agents = {}
        self.params = params
        self.mavrosNamespaces = mavrosNamespaces
        self.mapOrigin = None  # PoseStamped
        self.tf2Br = None

        for i, agent in enumerate(mavrosNamespaces):
            self.agents[agent] = {
                'reached': False,
                'localTransformMap': localTransforms[i][0],
                'localTransformBaseLink': localTransforms[i][1],
                'path': None,
                'currentPoseInPath': 0,  # counter for PoseStamped in Path
                'state': None,
                'localPose': None,
                'globalGps': None,
                'pathSubscriber': None,
                'stateSubscriber': None,
                'setPointPublisher': None,
                'localPositionSubscriber': None,
                'globalGpsPositionSubscriber': None,
                'setModeService': None,
                'armingService': None,
                'mapLPosePublisher': None,
                'mapGPointPublisher': None,
                'calibrationPath': None,
                'calibrationPathPublisher': None,
                'currentPoseInCalibrationPath': 0,
                'captureLocalPose': False,
                'captureGlobalGps': False,
                'calibLocalPoses': [],
                'calibGlobalGpses': [],
                'homography': None,
                'transform': {
                    'q': None,
                    't': None,
                },
            }
        self.subscribeMapOrigin()
        self.createTransformBroadcaster()
        self.createTransformListener()
        self.createMavrosServiceClients()
        self.createCalibrationPathPublisher()
        self.publishControllerState()
        self.publishAgentsMapPosition()
        self.subscribeAndPublishMavrosAgents()
        self.subscribePathTopics()
        self.createCalibrationPath()
    def subscribeMapOrigin(self):
        topic = self.params['mapOriginTopic']
        self.mapOriginSub = rospy.Subscriber(topic, PoseStamped, self.recvMapOrigin)

    def subscribePathTopics(self):
        for agent in self.agents:
            topic = '/pegasus/%s/path' % (agent,)
            rospy.loginfo('Listening to path topic: %s' % (topic,))
            self.agents[agent]['pathSubscriber'] = rospy.Subscriber(topic, Path, self.recvPegasusPath, (agent, topic))

    def subscribeAndPublishMavrosAgents(self):
        for agent in self.agents:
            stateTopic = '/%s/mavros/state' % (agent,)
            rospy.loginfo('Subscriber to agent state: %s' % (stateTopic,))
            self.agents[agent]['stateSubscriber'] = rospy.Subscriber(stateTopic, State,
                                                                     self.recvMavrosState, (agent, stateTopic))

            setPointTopic = '/%s/mavros/setpoint_position/local' % (agent,)
            rospy.loginfo('Publisher to setpoint_position: %s' % (setPointTopic,))
            self.agents[agent]['setPointPublisher'] = rospy.Publisher(setPointTopic, PoseStamped, queue_size=1000)

            localPositionTopic = '/%s/mavros/local_position/pose' % (agent,)
            rospy.loginfo('Subscriber to local position: %s' % (localPositionTopic,))
            self.agents[agent]['localPositionSubscriber'] = rospy.Subscriber(localPositionTopic, PoseStamped,
                                                                             self.recvMavrosPose,
                                                                             (agent, localPositionTopic))

            globalGpsTopic = '/%s/mavros/global_position/global' % (agent,)
            rospy.loginfo('Subscriber to global gps: %s' % (globalGpsTopic,))
            self.agents[agent]['globalGpsSubscriber'] = rospy.Subscriber(globalGpsTopic, NavSatFix,
                                                                         self.recvMavrosGlobalGps,
                                                                         (agent, globalGpsTopic))

    def publishControllerState(self):
        stateTopic = '/pegasus/state/controller'
        rospy.loginfo('Publisher controller state: %s' % (stateTopic,))
        self.statePublisher = rospy.Publisher(stateTopic, UInt8, queue_size=1000)

    def publishAgentsMapPosition(self):
        for agent in self.agents:
            mapLPositionTopic = '/pegasus/%s/position/localToMap' % (agent,)
            rospy.loginfo('Publisher to local to map position: %s' % (mapLPositionTopic,))
            self.agents[agent]['mapLPosePublisher'] = rospy.Publisher(mapLPositionTopic, PoseStamped, queue_size=1000)
            mapGPositionTopic = '/pegasus/%s/position/gpsToMap' % (agent,)
            rospy.loginfo('Publisher to gps to map point: %s' % (mapGPositionTopic,))
            self.agents[agent]['mapGPointPublisher'] = rospy.Publisher(mapGPositionTopic, PointStamped, queue_size=1000)

    def createMavrosServiceClients(self):
        for agent in self.agents:
            setModeService = '/%s/mavros/set_mode' % (agent,)
            armingService = '/%s/mavros/cmd/arming' % (agent,)
            rospy.wait_for_service(setModeService)
            rospy.loginfo('Set Mode service: %s' % (setModeService,))
            self.agents[agent]['setModeService'] = rospy.ServiceProxy(setModeService, SetMode)
            rospy.wait_for_service(setModeService)
            rospy.loginfo('Arming service: %s' % (armingService,))
            self.agents[agent]['armingService'] = rospy.ServiceProxy(armingService, CommandBool)

    def createTransformBroadcaster(self):
        self.tf2Br = tf2_ros.TransformBroadcaster()

    def createTransformListener(self):
        self.tfBuffer = tf2_ros.Buffer()
        self.transformListener = tf2_ros.TransformListener(self.tfBuffer)

    def createCalibrationPathPublisher(self):
        for agent in self.agents:
            calibrationPathTopic = '/pegasus/%s/calibration/path' % (agent,)
            rospy.loginfo('Publisher to calibration path: %s' % (calibrationPathTopic,))
            self.agents[agent]['calibrationPathPublisher'] = rospy.Publisher(calibrationPathTopic, Path, latch=True,
                                                                             queue_size=1000)

    def createCalibrationPath(self):
        sideLength = self.params['gridSize']
        agentsHoverHeight = self.params['agentsHoverHeight']
        boxPoints = ((0., 0.), (sideLength, 0.), (sideLength, sideLength), (0., sideLength))
        # boxPoints = ((0, 0), (sideLength, 0))
        numPoints = len(boxPoints)
        vectors1 = np.array(boxPoints)
        vectors2 = np.zeros((numPoints, 2))
        vectors2[1:] = vectors1[0:-1]
        v1sv2 = vectors1 - vectors2
        direction = np.arctan2(v1sv2[:, 1], v1sv2[:, 0])
        for i, agent in enumerate(self.agents.values()):
            agent['calibrationPath'] = Path()
            for k in range(numPoints):
                yaw = direction[k]
                q = quaternion_from_euler(0, 0, yaw)
                pose = PoseStamped()
                pose.header.frame_id = agent['localTransformMap']
                pose.pose.position.x = boxPoints[k][0]
                pose.pose.position.y = boxPoints[k][1]
                pose.pose.position.z = agentsHoverHeight + (i * 2)
                pose.pose.orientation.x = q[0]
                pose.pose.orientation.y = q[1]
                pose.pose.orientation.z = q[2]
                pose.pose.orientation.w = q[3]
                agent['calibrationPath'].header.frame_id = agent['localTransformMap']
                agent['calibrationPath'].poses.append(pose)
                agent['calibrationPathPublisher'].publish(agent['calibrationPath'])

    def recvPegasusPath(self, data, args):
        agent = args[0]
        self.agents[agent]['path'] = data
        # rospy.loginfo("Received path for %s" % (agent, ))

    def recvMavrosState(self, data, args):
        agent = args[0]
        self.agents[agent]['state'] = data
        # print(data)

    def recvMavrosPose(self, data, args):
        agent = args[0]
        self.agents[agent]['localPose'] = data
        if self.agents[agent]['captureLocalPose']:
            print ('capturing local pose for %s' % (agent,))
            self.agents[agent]['captureLocalPose'] = False
            self.agents[agent]['calibLocalPoses'].append(data)

        now = rospy.get_rostime()

        localMap = self.agents[agent]['localTransformMap']
        try:
            trans = self.tfBuffer.lookup_transform('map', localMap, rospy.Time())
            transformedPose = tf2_geometry_msgs.do_transform_pose(data, trans)
            self.agents[agent]['mapLPosePublisher'].publish(transformedPose)
        except (tf2_ros.LookupException, tf2_ros.ConnectivityException, tf2_ros.ExtrapolationException) as e:
            # rospy.loginfo(e)
            return None

    def recvMavrosGlobalGps(self, data, args):
        agent = args[0]
        utmPos = utm.fromLatLong(data.latitude, data.longitude, data.altitude)
        pt = utmPos.toPoint()
        d = np.array((pt.x, pt.y, pt.z))
        self.agents[agent]['globalGps'] = d
        if self.agents[agent]['captureGlobalGps']:
            print ('capturing globalpose for %s' % (agent,))
            self.agents[agent]['captureGlobalGps'] = False
            self.agents[agent]['calibGlobalGpses'].append(d)

        mapPoint = PointStamped()
        mapPoint.header.frame_id = 'map'
        mapPt = np.subtract(d, self.mapOrigin)
        mapPoint.point.x = mapPt[0]
        mapPoint.point.y = mapPt[1]
        mapPoint.point.z = mapPt[2]
        self.agents[agent]['mapGPointPublisher'].publish(mapPoint)

    def recvMapOrigin(self, data):
        mapOriginUTM = utm.fromLatLong(
            data.pose.position.y,
            data.pose.position.x,
            data.pose.position.z)

        self.mapOrigin = np.array((
            mapOriginUTM.toPoint().x,
            mapOriginUTM.toPoint().y,
            mapOriginUTM.toPoint().z))

        self.mapOriginSub.unregister()

    def broadcastTransforms(self):
        for agent in self.agents.values():
            if agent['transform']['q'] is not None and agent['transform']['t'] is not None:
                t = TransformStamped()
                t.header.stamp = rospy.Time.now()
                t.header.frame_id = 'map'
                t.child_frame_id = agent['localTransformMap']
                t.transform.translation.x = agent['transform']['t'][0]
                t.transform.translation.y = agent['transform']['t'][1]
                t.transform.translation.z = 0
                t.transform.rotation.x = agent['transform']['q'][0]
                t.transform.rotation.y = agent['transform']['q'][1]
                t.transform.rotation.z = agent['transform']['q'][2]
                t.transform.rotation.w = agent['transform']['q'][3]

                self.tf2Br.sendTransform(t)
'''

"""
import pydevd_pycharm
pydevd_pycharm.settrace('localhost', port=7778, stdoutToServer=True, stderrToServer=True)
"""
if __name__ == '__main__':
    rospy.init_node('pegasus_controller')
    rospy.loginfo('Starting pegasus_controller...')
    agents = rospy.get_param('~agents')
    z_height = rospy.get_param('~agents_hover_height')
    grid_size = rospy.get_param('~grid_size')
    map_origin_topic = rospy.get_param('~map_origin_topic')
    rospy.loginfo(agents[0])

    controller = PegasusController({
        'agents': agents,
        'z_height': z_height,
        'grid_size': grid_size,
        'map_origin_topic': map_origin_topic,
    })
    #    , localTransforms,
    #                                      {'agentsHoverHeight': float(zHeight), 'gridSize': float(gridSize),
    #                                       'mapOriginTopic': mapOriginTopic})
    controller_thread = ControllerThread(controller, 0)

    controller_thread.start()

    controller.spin()
    rospy.spin()
