# Retail Demo Store Waypoint Service

The Waypoint web service provides an API for retrieving store locations and customer routes to support to Waypoint NRF demo.  

This service has two endpoints

## Local Development

The Waypoint service can be built and run locally (in Docker) using Docker Compose. See the [local development instructions](../) for details. **From the `../src` directory**, run the following command to build and deploy the service locally.

```console
foo@bar:~$ docker-compose up --build waypoint
```

Once the container is up and running, you can access it in your browser or with a utility such as [Postman](https://www.postman.com/) at [http://localhost:8008](http://localhost:8009).
