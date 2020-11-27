import logging
import os
import random
import string
import json

from crhelper import CfnResource
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RESOURCE_BUCKET = os.environ.get('RESOURCE_BUCKET')

helper = CfnResource()
location = boto3.client('location')
s3 = boto3.resource('s3')


def load_default_geofence_from_s3():
    """ Retrieves GeoJson file containing store Geofence from S3 """
    route_file_obj = s3.Object(RESOURCE_BUCKET, 'waypoint/store_geofence.json')
    store_geofence = json.loads(route_file_obj.get()['Body'].read().decode('utf-8'))
    return store_geofence


def get_random_string(length):
    """ Generates a random string of upper & lower case letters. """
    letters = string.ascii_letters
    rand_string = ''.join(random.choice(letters) for i in range(length))
    return rand_string


def get_geofence_collection_arn(region, account_id, collection_name):
    """ Helper to convert Geofence Collection name to ARN, since this information is not available directly from the
    Geofencing API. """
    return f"arn:aws:geo:{region}:{account_id}:geofencecollection/{collection_name}"


def get_default_geofence_id(resource_name):
    """ Helper to ensure consistency when referring to default Geofence by id (name) """
    return f"{resource_name}-default-geofence"


def put_default_geofence(resource_name):
    """ Creates a default Geofence around central London """
    geofence_geojson = load_default_geofence_from_s3()

    default_geofence = {
        "Polygon": geofence_geojson['features'][0]['geometry']['coordinates']
    }
    logger.info(f"Creating default Geofence: {default_geofence}")
    response = location.put_geofence(
        CollectionName=resource_name,
        Geometry=default_geofence,
        GeofenceId=get_default_geofence_id(resource_name)
    )
    logger.info(response)


@helper.create
def create(event, context):
    stack_name = event['StackName']
    region = event['Region']
    account_id = event['AccountId']
    create_default_geofence = event['ResourceProperties']['CreateDefaultGeofence'].lower() == 'true'

    # Generate the resource name to be used for all Waypoint resources
    resource_name = stack_name + '-' + get_random_string(8)
    helper.PhysicalResourceId = resource_name
    helper.Data.update({'WaypointResourceName': resource_name})

    # Create a map
    logger.info(f"Creating Map with name: {resource_name}")
    response = location.create_map(
        MapName=resource_name,
        Configuration={
            'Style': 'VectorEsriNavigation'
        },
        Description=f'Map belonging to CloudFormation Stack {stack_name}'
    )
    logger.info(response)

    # Create a geofence collection
    logger.info(f"Creating Geofence Collection with name: {resource_name}")
    response = location.create_geofence_collection(
        CollectionName=resource_name,
        Description=f'Collection belonging to CloudFormation Stack {stack_name}'
    )
    logger.info(response)
    collection_arn = get_geofence_collection_arn(region, account_id, resource_name)

    # Create a geofence
    if create_default_geofence:
        put_default_geofence(resource_name)

    # Create a tracker
    logger.info(f"Creating Tracker with name: {resource_name}")
    response = location.create_tracker(
        TrackerName=resource_name,
        Description=f'Tracker belonging to CloudFormation Stack {stack_name}'
    )
    logger.info(response)

    # Associate tracker consumer ie. link tracker & geofence
    logger.info(f"Associating Tracker {resource_name} with Geofence Collection {resource_name} (ARN: {collection_arn})")
    response = location.associate_tracker_consumer(
        ConsumerArn=collection_arn,
        TrackerName=resource_name
    )
    logger.info(response)

    logger.info("Creation complete.")
    return


@helper.update
def update(event, context):
    resource_name = event['PhysicalResourceId']
    create_default_geofence = event['ResourceProperties']['CreateDefaultGeofence'].lower() == 'true'
    previous_create_default_geofence = event['OldResourceProperties']['CreateDefaultGeofence'].lower() == 'true'

    if create_default_geofence != previous_create_default_geofence:
        if create_default_geofence:
            put_default_geofence(resource_name)
        else:
            geofence_id = get_default_geofence_id(resource_name)
            logger.info(f"Deleting Geofence: {geofence_id}")
            try:
                response = location.batch_delete_geofence(
                    CollectionName=resource_name,
                    GeofenceIds=[geofence_id]
                )
                logger.info(response)
            except location.exceptions.ResourceNotFoundException:
                logger.warning(f"Geofence could not be deleted as does not exist: {geofence_id}")

    logger.info("Update complete.")
    return


@helper.delete
def delete(event, context):
    resource_name = event['PhysicalResourceId']

    # Delete Map
    logger.info(f"Deleting Map: {resource_name}")
    try:
        response = location.delete_map(MapName=resource_name)
        logger.info(response)
    except location.exceptions.ResourceNotFoundException:
        logger.info(f"Map {resource_name} does not exist, nothing to delete")

    # Delete Geofence Collection
    logger.info(f"Deleting Geofence Collection: {resource_name}")
    try:
        response = location.delete_geofence_collection(CollectionName=resource_name)
        logger.info(response)
    except location.exceptions.ResourceNotFoundException:
        logger.info(f"Geofence Collection {resource_name} does not exist, nothing to delete")

    # Delete Tracker
    logger.info(f"Deleting Tracker: {resource_name}")
    try:
        response = location.delete_tracker(TrackerName=resource_name)
        logger.info(response)
    except location.exceptions.ResourceNotFoundException:
        logger.info(f"Tracker {resource_name} does not exist, nothing to delete")

    logger.info("Deletion complete.")
    return


def lambda_handler(event, context):
    logger.info('Environment:')
    logger.info(os.environ)
    logger.info('Event:')
    logger.info(event)

    # Set stack name in the event here once for use across all handlers
    event['StackName'] = event['StackId'].split('/')[-2]
    event['Region'] = event['StackId'].split(':')[3]
    event['AccountId'] = event['StackId'].split(':')[4]

    helper(event, context)
