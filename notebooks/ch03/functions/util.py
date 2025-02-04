import os
import datetime
import time
import requests
import pandas as pd
import json
from geopy.geocoders import Nominatim
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import MultipleLocator
import openmeteo_requests
import requests_cache
from retry_requests import retry
import hopsworks
import hsfs
from pathlib import Path
import urllib.request


def get_historical_weather(city, start_date, end_date, latitude, longitude):
    # latitude, longitude = get_city_coordinates(city)

    # Setup the Open-Meteo API client with cache and retry on error
    cache_session = requests_cache.CachedSession(".cache", expire_after=-1)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    # Make sure all required weather variables are listed here
    # The order of variables in hourly or daily is important to assign them correctly below
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ["precipitation", "temperature_2m"],
    }
    responses = openmeteo.weather_api(url, params=params)

    # Process first location. Add a for-loop for multiple locations or weather models
    response = responses[0]
    print(f"Coordinates {response.Latitude()}°N {response.Longitude()}°E")
    print(f"Elevation {response.Elevation()} m asl")
    print(f"Timezone {response.Timezone()} {response.TimezoneAbbreviation()}")
    print(f"Timezone difference to GMT+0 {response.UtcOffsetSeconds()} s")

    hourly = response.Hourly()
    hourly_precipitation = hourly.Variables(0).ValuesAsNumpy()
    temperature_2m_mean = hourly.Variables(1).ValuesAsNumpy()

    hourly_data = {
        "date": pd.date_range(
            start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
            end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left",
        )
    }
    hourly_data["precipitation"] = hourly_precipitation
    hourly_data["temperature"] = temperature_2m_mean
    hourly_data["date"] = pd.to_datetime(hourly_data["date"])

    hourly_dataframe = pd.DataFrame(data=hourly_data)
    hourly_dataframe = hourly_dataframe.dropna()
    hourly_dataframe["city"] = city
    return hourly_dataframe


def get_hourly_weather_forecast(city, latitude, longitude):

    # latitude, longitude = get_city_coordinates(city)

    # Setup the Open-Meteo API client with cache and retry on error
    cache_session = requests_cache.CachedSession(".cache", expire_after=3600)
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    openmeteo = openmeteo_requests.Client(session=retry_session)

    # Make sure all required weather variables are listed here
    # The order of variables in hourly or daily is important to assign them correctly below
    url = "https://api.open-meteo.com/v1/ecmwf"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": [
            "temperature_2m",
            "precipitation",
        ],
    }
    responses = openmeteo.weather_api(url, params=params)

    # Process first location. Add a for-loop for multiple locations or weather models
    response = responses[0]
    print(f"Coordinates {response.Latitude()}°N {response.Longitude()}°E")
    print(f"Elevation {response.Elevation()} m asl")
    print(f"Timezone {response.Timezone()} {response.TimezoneAbbreviation()}")
    print(f"Timezone difference to GMT+0 {response.UtcOffsetSeconds()} s")

    # Process hourly data. The order of variables needs to be the same as requested.

    hourly = response.Hourly()
    hourly_temperature_2m = hourly.Variables(0).ValuesAsNumpy()
    hourly_precipitation = hourly.Variables(1).ValuesAsNumpy()

    hourly_data = {
        "date": pd.date_range(
            start=pd.to_datetime(hourly.Time(), unit="s"),
            end=pd.to_datetime(hourly.TimeEnd(), unit="s"),
            freq=pd.Timedelta(seconds=hourly.Interval()),
            inclusive="left",
        )
    }
    hourly_data["temperature"] = hourly_temperature_2m
    hourly_data["precipitation"] = hourly_precipitation
    hourly_data["date"] = pd.to_datetime(hourly_data["date"])

    hourly_dataframe = pd.DataFrame(data=hourly_data)
    hourly_dataframe = hourly_dataframe.dropna()
    return hourly_dataframe


def get_city_coordinates(city_name: str):
    """
    Takes city name and returns its latitude and longitude (rounded to 2 digits after dot).
    """
    # Initialize Nominatim API (for getting lat and long of the city)
    geolocator = Nominatim(user_agent="MyApp")
    city = geolocator.geocode(city_name)

    latitude = round(city.latitude, 2)
    longitude = round(city.longitude, 2)

    return latitude, longitude


def trigger_request(url: str):
    response = requests.get(url)
    if response.status_code == 200:
        # Extract the JSON content from the response
        data = response.json()
    else:
        print("Failed to retrieve data. Status Code:", response.status_code)
        raise requests.exceptions.RequestException(response.status_code)

    return data


def get_pm25(
    aqicn_url: str,
    country: str,
    city: str,
    street: str,
    day: datetime.date,
    AQI_API_KEY: str,
):
    """
    Returns DataFrame with air quality (pm25) as dataframe
    """
    # The API endpoint URL
    url = f"{aqicn_url}/?token={AQI_API_KEY}"

    # Make a GET request to fetch the data from the API
    data = trigger_request(url)

    # if we get 'Unknown station' response then retry with city in url
    if data["data"] == "Unknown station":
        url1 = f"https://api.waqi.info/feed/{country}/{street}/?token={AQI_API_KEY}"
        data = trigger_request(url1)

    if data["data"] == "Unknown station":
        url2 = (
            f"https://api.waqi.info/feed/{country}/{city}/{street}/?token={AQI_API_KEY}"
        )
        data = trigger_request(url2)

    # Check if the API response contains the data
    if data["status"] == "ok":
        # Extract the air quality data
        aqi_data = data["data"]
        aq_today_df = pd.DataFrame()
        aq_today_df["pm25"] = [aqi_data["iaqi"].get("pm25", {}).get("v", None)]
        aq_today_df["pm25"] = aq_today_df["pm25"].astype("float32")

        aq_today_df["country"] = country
        aq_today_df["city"] = city
        aq_today_df["street"] = street
        aq_today_df["date"] = day
        aq_today_df["date"] = pd.to_datetime(aq_today_df["date"])
        aq_today_df["url"] = aqicn_url
    else:
        print(
            "Error: There may be an incorrect  URL for your Sensor or it is not contactable right now. The API response does not contain data.  Error message:",
            data["data"],
        )
        raise requests.exceptions.RequestException(data["data"])

    return aq_today_df


def plot_bikes_prediction(df: pd.DataFrame, file_path: str, hindcast=False):
    fig, ax = plt.subplots(figsize=(10, 6))

    # get the date and the hour from the date column
    day = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d %H:00")
    # Plot each column separately in matplotlib
    ax.plot(
        day,
        df["predicted_num_bikes_available"],
        label="Predicted bikes",
        color="red",
        linewidth=2,
        marker="o",
        markersize=5,
        markerfacecolor="blue",
    )

    # Set the y-axis to a linear scale
    ax.set_yscale("linear")
    ax.set_yticks([0, 10, 25])
    ax.get_yaxis().set_major_formatter(plt.ScalarFormatter())
    ax.set_ylim(bottom=1)

    # Set the labels and title
    ax.set_xlabel("Date")
    ax.set_title(
        f"Bikes prediction in station C/ CIUTAT DE GRANADA, 168 | AV. DIAGONAL"
    )
    ax.set_ylabel("Bikes")

    colors = ["red", "orange", "yellow", "green"]
    labels = [
        "Few",
        "Some",
        "Several",
        "Many",
    ]
    ranges = [(0, 5), (5, 10), (10, 15), (15, 25)]
    for color, (start, end) in zip(colors, ranges):
        ax.axhspan(start, end, color=color, alpha=0.3)

    # Add a legend for the different Air Quality Categories
    patches = [
        Patch(color=colors[i], label=f"{labels[i]}: {ranges[i][0]}-{ranges[i][1]}")
        for i in range(len(colors))
    ]
    legend1 = ax.legend(
        handles=patches,
        loc="upper right",
        title="Bikes prediction",
        fontsize="x-small",
    )

    # Aim for ~10 annotated values on x-axis, will work for both forecasts ans hindcasts
    if len(df.index) > 5:
        every_x_tick = len(df.index) / 4
        ax.xaxis.set_major_locator(MultipleLocator(every_x_tick))

    plt.xticks(rotation=45)

    if hindcast == True:
        ax.plot(
            day,
            df["num_bikes_available"],
            label="Actual number of bikes",
            color="black",
            linewidth=2,
            marker="^",
            markersize=5,
            markerfacecolor="grey",
        )
        legend2 = ax.legend(loc="upper left", fontsize="x-small")
        ax.add_artist(legend1)

    # Ensure everything is laid out neatly
    plt.tight_layout()

    # # Save the figure, overwriting any existing file with the same name
    plt.savefig(file_path)
    return plt


def delete_feature_groups(fs, name):
    try:
        for fg in fs.get_feature_groups(name):
            fg.delete()
            print(f"Deleted {fg.name}/{fg.version}")
    except hsfs.client.exceptions.RestAPIError:
        print(f"No {name} feature group found")


def delete_feature_views(fs, name):
    try:
        for fv in fs.get_feature_views(name):
            fv.delete()
            print(f"Deleted {fv.name}/{fv.version}")
    except hsfs.client.exceptions.RestAPIError:
        print(f"No {name} feature view found")


def delete_models(mr, name):
    models = mr.get_models(name)
    if not models:
        print(f"No {name} model found")
    for model in models:
        model.delete()
        print(f"Deleted model {model.name}/{model.version}")


def delete_secrets(proj, name):
    secrets = secrets_api(proj.name)
    try:
        secret = secrets.get_secret(name)
        secret.delete()
        print(f"Deleted secret {name}")
    except hopsworks.client.exceptions.RestAPIError:
        print(f"No {name} secret found")


# WARNING - this will wipe out all your feature data and models


def purge_project(proj):
    fs = proj.get_feature_store()
    mr = proj.get_model_registry()

    # Delete Feature Views before deleting the feature groups
    delete_feature_views(fs, "air_quality_fv")

    # Delete ALL Feature Groups
    delete_feature_groups(fs, "air_quality")
    delete_feature_groups(fs, "weather")
    delete_feature_groups(fs, "aq_predictions")

    # Delete all Models
    delete_models(mr, "air_quality_xgboost_model")
    delete_secrets(proj, "SENSOR_LOCATION_JSON")


def check_file_path(file_path):
    my_file = Path(file_path)
    if my_file.is_file() == False:
        print(f"Error. File not found at the path: {file_path} ")
    else:
        print(f"File successfully found at the path: {file_path}")


def backfill_predictions_for_monitoring(weather_fg, bikes_df, monitor_fg, model):
    features_df = weather_fg.read()
    features_df = features_df.sort_values(by=["date"], ascending=True)
    features_df = features_df.tail(10)
    features_df["predicted_num_bikes_available"] = model.predict(
        features_df[
            [
                "is_weekend",
                "is_holiday",
                "prev_num_bikes_available",
                "precipitation",
                "temperature",
                "time",
            ]
        ]
    )
    df = pd.merge(
        features_df, bikes_df[["date", "pm25", "street", "country"]], on="date"
    )
    df["days_before_forecast_day"] = 1
    hindcast_df = df
    df = df.drop("pm25", axis=1)
    monitor_fg.insert(df, write_options={"wait_for_job": True})
    return hindcast_df


def fetch_station_data(url, authorization_token, target_station_id=42):
    """
    Returns DataFrame with station data for a specific station ID.
    """
    request = urllib.request.Request(url)
    request.add_header("Authorization", authorization_token)

    with urllib.request.urlopen(request) as response:
        data = response.read()

        try:
            stations_data = json.loads(data)

            if "data" in stations_data and "stations" in stations_data["data"]:
                stations = stations_data["data"]["stations"]

                filtered_station = next(
                    (s for s in stations if s["station_id"] == target_station_id), None
                )

                if filtered_station:
                    reported = filtered_station["last_reported"]

                    station_df = pd.DataFrame(
                        {
                            "station_id": [filtered_station["station_id"]],
                            "num_bikes_available": [
                                filtered_station["num_bikes_available"]
                            ],
                            "last_reported": reported,
                        }
                    )
                    station_df["last_reported"] = pd.to_datetime(
                        station_df["last_reported"], unit="s", utc=True, errors="coerce"
                    )
                    return station_df
                else:
                    print(f"Station ID {target_station_id} not found.")
            else:
                print("'stations' key not found in the response.")
        except json.JSONDecodeError:
            print("Failed to decode JSON")
            print(data)

    return pd.DataFrame()  # Return empty DataFrame if no data is found
