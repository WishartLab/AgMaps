#
# -----RUN WEBASSMEBLY-----
# This file contains the ShinyLive application for choropleth + coordinate maps.
# It can be run with the following command within this directory:
#		shinylive export . [site]
# Where [site] is the destination of the site folder.
#
# -----RUN PYTHON-----
# If you would rather deploy the application as a PyShiny application,
# run the following command within this directory:
#		shiny run
#


from shiny import App, reactive, render, ui
from folium import Map as FoliumMap, Choropleth, Circle, GeoJson, Rectangle
from folium.features import GeoJsonTooltip
from folium.plugins import TimeSliderChoropleth, HeatMap as FoliumHeatMap, HeatMapWithTime
from pandas import DataFrame
from branca.colormap import linear, LinearColormap, StepColormap
from pathlib import Path
from json import loads
from datetime import datetime
from time import mktime
from scipy.stats import gaussian_kde
from scipy.interpolate import griddata
from numpy import vstack, linspace, meshgrid
from math import sqrt
import re

from shared import Cache, Colors, Inlineify, NavBar, MainTab, Pyodide, Filter, ColumnType, TableOptions, Raw, InitializeConfig, ColorMaps, Error, Update, Msg, File
from geojson import Mappings

try:
	from user import config
except ImportError:
	from config import config

# Fine, Shiny
import branca, certifi, xyzservices

URL = f"{Raw}/geomap/data/" if Pyodide else "../data/"

def server(input, output, session):

	InfoChoropleth = {
		"Backyard_Hens_and_Bees.csv": '''<u>Input type:</u> .csv Data <br><u>Contents:</u> Number of properties with hens or bees in Edmonton, by neighbourhood.''',
		"example6.csv": '''<u>Input type:</u> .csv Data<br> <u>Contents:</u> COVID-19 information by province or territory, reported by the Canadian Government, from February 8, 2020 to February 17, 2024. The 'Value Column' dropdown indicates which column is visualized. <br><u>Source:</u> <a href="https://open.canada.ca/data/en/dataset/261c32ab-4cfd-4f81-9dea-7b64065690dc/resource/39434379-45a1-43d5-aea7-a7a50113c291"; target="_blank">Open Data Portal</a>''',
	}

	def HandleData(paths:list, p=None):
		"""
		@brief A custom Data Handler for the Cache.
		@param paths: Paths to files to load
		@returns A data object from the cache.
		@info This Data Handler supports geojson files as json
		"""
		# load the file(s) as a dataframe
		return DataCache.DefaultHandler(paths)
	DataCache = Cache("geomap", DataHandler=HandleData)
	DataChoropleth = reactive.value(None)
	Valid = reactive.value(False)
	JSON = reactive.value(None)
	DataCoordinate = reactive.value(None)

	InitializeConfig(config, input)


	async def MakeColorSelectors(column_name):
		"""
		@info For each category of choropleth data, create a colour selector ui element.
		"""
		try:
			# remove old colour dropdowns
			ui.remove_ui(selector="#dynamic_colour_dropdowns")
		except Exception as e:
			print(f"Error removing previous colour selectors:\n{e}")

		# get all categories
		categories = DataChoropleth()[column_name].unique()

		# get all colors
		color_keys = list(Colors)
		color_index = 0

		all_color_select = []
		for category in categories:
			# remove invalid characters for id name
			category_clean = re.sub(r"\s+", "", str(category))
			category_clean = re.sub(f"[^a-zA-Z0-9]", "_", category)
			color_select = ui.input_select(id=f"ColorSelect{category_clean}", label=f"{category}", choices=Colors, multiple=False, selectize=True, selected=color_keys[color_index])
			all_color_select.append(color_select)
			if color_index < (len(color_keys) - 1):
				color_index += 1

		accordion = ui.accordion(
			ui.accordion_panel(
				"Choropleth Colors",
				*all_color_select,
			), 
			id="dynamic_colour_dropdowns"
		)
		ui.insert_ui(
			accordion,
			selector="#ChoroplethSettings",
			where="beforeEnd",
		)


	@reactive.effect
	@reactive.event(input.Example, input.Reset)
	async def UpdateDataChoropleth():
		"""
		@info Update data when the choropleth data file is selected or modified.
		"""
		p = ui.Progress()
		try:
			DataChoropleth.set((await DataCache.Load(
				input, 
				p=p,
				input_switch="Example",
				example="Example",
			)))
			Valid.set(False)

			columns = DataChoropleth().columns
			key = Filter(columns, ColumnType.Name, id="KeyColumn")
			val = Filter(columns, ColumnType.Value, id="ValueColumn", all=True)
			if val:
				choice = 0
				while choice < len(val) and val[choice] == key: choice += 1
				ui.update_select(id="ValueColumn", selected=val[choice])
				
				# make colour selectors for each category in val column
				await MakeColorSelectors(val[choice])

			DataCache.Invalidate(File(input))
		except Exception as e:
			print(f"choropleth error: {e}")
			p.close()
			Error("File could not be loaded!\nChoropleth data can be uploaded as a .csv, .tsv, .txt, .xslx, .dat, .tab, or .odf file.")
			return


	@reactive.effect
	@reactive.event(input.JSONUpload, input.JSONSelection, input.JSONFile)
	async def UpdateGeoJSON():
		"""
		Update data when the choropleth GeoJSON file is selected or modified.
		"""
		JSON.set(await DataCache.Load(
			input,
			source_file=input.JSONUpload(),
			example_file=input.JSONSelection(),
			source=URL,
			input_switch=input.JSONFile(),
			example="Provided",
			default=None,
			p=ui.Progress(),
			p_name="GeoJSON"
		))
		geojson = JSON()

		if geojson is None: return
		properties = list(geojson['features'][0]['properties'].keys())
		Filter(properties, ColumnType.NameGeoJSON, id="KeyProperty")


	async def MakeSettingsForLayers(col_options:dict):
		"""
		@info When coordinate files are uploaded, make a settings dropdown for each file.
		"""
		if input.CoordinateUpload():
			try:
				# remove old layer settings dropdowns
				ui.remove_ui(selector="#dynamic_accordion")
			except Exception as e:
				print(f"Error removing previous file settings:\n{e}")
			
			settings_dropdowns = []
			for file in input.CoordinateUpload():
				filename = file["name"]
				name = filename.split(".")[0]
				dropdown = ui.accordion_panel(
					f"{filename}", 
					# enable/disable layer
					ui.input_checkbox_group(id=f"Disable{name}", inline=False, label=None, choices=["Disable Layer"], selected=None),
					
					#config.TimeColumn.UI(ui.input_select, id=f"TimeColumn{name}", label="Time Column", choices=col_options[f"TimeColumn{name}"], multiple=False, tooltip="Optional: Specify a time column to plot data over time. If an explicit time column is specified, data can be visualized temporally with a media-player-like interface (play, pause, rewind, and frame speed options). If 'None' is selected, the heatmap will be static."),
					config.ValueColumnCoord.UI(ui.input_select, id=f"ValueColumnCoord{name}", label="Value Column", choices=[col_options[f"ValueColumnCoord{name}"], "Uniform"], selected="Uniform", multiple=False, tooltip="If a column from the input data is specified, values from that column will be associated with each latitude, longitude point, and the point will be colored based on its value. If 'Uniform' is selected, data points will be assigned a uniform value and colored uniformly on the map."),
					# Colour data points by density, instead of assigned values. Uses kernel density estimation
					ui.panel_conditional(
						f"input.ValueColumnCoord{name} !== 'Uniform'",
						ui.input_checkbox_group(id=f"KDE{name}", inline=False, label=None, choices=["Color by Density"], selected=None),
					),
					# select colours
					ui.panel_conditional(
						f"input.RenderMode{name} === 'Vector'",
						ui.input_select(id=f"CustomColors{name}", label="Colors", choices=Colors, multiple=True, selectize=True, selected=["#ff0000","#ff9900","#fff200","#00ff80","#00bfff","#8000ff",]),
					),
					# point radius
					config.Radius.UI(ui.input_numeric, id=f"Radius{name}", label="Data Point Size", min=5, tooltip="Specify how large each data point should be on the map."),
					# opacity
					config.OpacityCoord.UI(ui.input_slider, id=f"OpacityCoord{name}", label="Opacity", min=0.0, max=1.0, step=0.1, tooltip="Specify the opacity of the heatmap. 1.0 indicates full opacity, while lower values make the background map more visible."),
					# raster/vector
					config.RenderMode.UI(ui.input_select, id=f"RenderMode{name}", label="Render Mode", choices=["Raster", "Vector"], tooltip="Display data as discrete vector points, or a smooth raster shape (vector does not apply to temporal heatmaps). The intensity of raster points scales when the map is zoomed in or out. Vector points maintain a constant intensity regardless of zoom, but are more computationally expensive."),
					# if vector - shape
					# TODO: fix!!!
					ui.panel_conditional(
						f"input.RenderMode{name} === 'Vector'",
						config.RenderShape.UI(ui.input_select, id=f"RenderShape{name}", label="Vector Shape", choices=["Circle", "Rectangle"], tooltip="Specify the shape of vector points. Rectangular points are useful for contiguous data (like temperature or rainfall), while circular points are useful for discrete data (like disease cases or wildlife sightings)."),
					),
					# if raster - blurring
					ui.panel_conditional(
						f"input.RenderMode{name} === 'Raster'",
						config.Blur.UI(ui.input_slider, id=f"Blur{name}", label="Blurring", min=1, max=30, step=1, tooltip="Specify how much neighbouring points bleed into one another. Higher values make the heatmap appear more homogeneous, while lower values emphasize individual points. This applies to raster heatmaps only."),
					),
					# ROI
					ui.HTML("<b>Range of Interest</b>"),
					config.ROICoord.UI(ui.input_checkbox, make_inline=False, id=f"ROICoord{name}", label="Enable Range of Interest", tooltip="Define a minimum and maximum bound (inclusive) for data points. Select 'Remove' to ignore all data points outside of the range. Select 'Round' to round data points outside of the range to the maximum or minimum value. This setting is not applicable if 'Uniform' values are used."),
					config.ROI_ModeCoord.UI(ui.input_radio_buttons, make_inline=False, id=f"ROI_ModeCoord{name}", label=None, choices=["Remove", "Round"], inline=True, tooltip="Remove data points outside the range of interest, or round them to the maximum or minimum value"),
					ui.layout_columns(
						config.MinCoord.UI(ui.input_numeric,make_inline=False, id=f"Min{name}", label=None, min=0, tooltip="Minimum displayed value in range of interest (inclusive)."),
						config.MaxCoord.UI(ui.input_numeric, make_inline=False, id=f"Max{name}", label=None, min=0, tooltip="Maximum displayed value in range of interest (inclusive)."),
					),
				)
				settings_dropdowns.append(dropdown)

			accordion = ui.accordion(*settings_dropdowns, id="dynamic_accordion")
			ui.insert_ui(
				accordion,
				selector="#ChoroplethSettings",
				where="afterEnd",
			)


	@reactive.effect
	@reactive.event(input.CoordinateFiles, input.CoordinateUpload, input.CoordinateSelection)
	async def UpdateCoordinateData():
		"""
		Update data when the coordinate data files are selected or modified.
		"""
		p = ui.Progress()

		#if input.CoordinateSelection():
		if input.CoordinateUpload():
			col_options = {}
			try:
				DataCoordinate.set((await DataCache.Load(
					input, 
					source_file=input.CoordinateUpload(),  # list of dicts
					example_file=input.CoordinateSelection(),
					input_switch=input.CoordinateFiles(),
					example="Example",
					p=p,
					p_name="coordinate data"
					)
				))
				Valid.set(False)  # list of df?
				data = DataCoordinate()
				
				# for file in input.CoordinateSelection():
				# 	print(f"\nCoordinateSelection file:\n{file}\n")

				for file in input.CoordinateUpload():
					file_df = data[str(file["datapath"])]

					filename = file["name"]
					name = filename.split(".")[0]
					# get columns per file
					# filter columns to get time and value dropdown options
					columns = file_df.columns
					time = Filter(columns, ColumnType.Time, good=["None"], all=True)
					val = Filter(columns, ColumnType.Value, good=["Uniform"])

					# make UI elements
					col_options[f"TimeColumn{name}"] = time
					col_options[f"ValueColumnCoord{name}"] = val
				
				await MakeSettingsForLayers(col_options)

				DataCache.Invalidate(File(input))

			except Exception as e:
				print(f"coordinate error: {e}")
				#p.close()
				Error(ui.HTML("File could not be loaded!\nCoordinate data can be uploaded as a .csv, .tsv, .txt, .xslx, .dat, .tab, or .odf file."), e)
				return


	def GetDataChoropleth(): return Table.data_view() if Valid() else DataChoropleth()

	# TODO: add table for geocoordinate file(s)
	def GetDataCoordinate(): 
		return DataCoordinate()  # dict of {datapath: df}


	def CustomChoroplethColorMap(categories:list):
		"""
		@info Map categorical values to colours based on ui selections.
		"""
		colors = []
		vmax = 0
		indices = []
		index = 0
		category_to_index = {}

		# map categories to indices
		for category in categories:
			category = re.sub(r"\s+", "", str(category))
			category = re.sub(f"[^a-zA-Z0-9]", "_", category)
			selected_color_ui = f"ColorSelect{category}"
			selected_color = getattr(input, selected_color_ui)()

			colors.append(selected_color)
			category_to_index[category] = index
			indices.append(index)
			vmax = index
			index += 1
		
		# return colormap function
		return StepColormap(
			colors,
			vmin=0, vmax=vmax,
			index=indices,
		)
			


	def LoadChoropleth(df, map, geojson, k_col, v_col, k_prop, p):
		"""
		@brief Applies a Choropleth to a Folium Map
		@param df: The DataFrame that contains information to plot
		@param map: The Folium map
		@param geojson: The geojson that contains territory info
		@param k_vol: The name of the column within df that contains names
		@param v_col: the name of the column within df that contains the values to plot.
		@param k_prop: 
		"""
		df_dict = df.set_index(k_col)[v_col]
		df_dict = df_dict.to_dict()

		categories = df[v_col].unique()
		opacity = config.Opacity()

		colors = []
		vmax = 0
		indices = []
		index = 0
		category_to_color = {}

		# map categories to indices
		for category in categories:
			category_name = re.sub(r"\s+", "", str(category))
			category_name = re.sub(f"[^a-zA-Z0-9]", "_", category)
			selected_color_ui = f"ColorSelect{category_name}"
			selected_color = getattr(input, selected_color_ui)()

			colors.append(selected_color)
			category_to_color[category] = selected_color
			indices.append(index)
			vmax = index
			index += 1

		# # custom colormap function
		# colormap = StepColormap(
		# 	colors=colors,
		# 	vmin=0, vmax=vmax-1,
		# 	index=indices,
		# 	caption=f"Choropleth {v_col}"
		# )
		
		# GeoJson(
		# 	geojson,
		# 	style_function=lambda x:
		# 	{
		# 		"fillColor": colormap(df_dict[x['properties']['name']]) if x['properties']['name'] in df_dict else "transparent",
		# 		"color": "black",
		# 		"weight": 0.5,
		# 		"fillOpacity": opacity,
		# 	}
		# ).add_to(map)

		GeoJson(
			geojson,
			style_function=lambda x:
			{
				"fillColor": category_to_color[(df_dict[x['properties']['name']])] if x['properties']['name'] in df_dict else "transparent",
				"color": "black",
				"weight": 0.5,
				"fillOpacity": opacity,
			},
			tooltip=GeoJsonTooltip(
				fields=["name"],
				aliases=[""],
			)
		).add_to(map)


	def GenerateCoordinateMap(df, map, name, val_col, lat_col, lon_col):
		"""
		@brief Generate a coordinate heatmap
		@param df A pandas DataFrame containing the data to map
		@param map A folium map to add data to
		@param name The name of file that this data is from (just used to identify the correct settings ui elements)
		@param val_col
		@param lat_col
		@param lon_col
		"""
		disable_name = f"Disable{name}"
		disable = getattr(input, disable_name)()
		opacity_name = f"OpacityCoord{name}"
		opacity = getattr(input, opacity_name)()
		radius_name = f"Radius{name}"
		radius = getattr(input, radius_name)()
		blur_name = f"Blur{name}"
		blur = getattr(input, blur_name)()
		render_name = f"RenderMode{name}"
		render = getattr(input, render_name)()
		kde_config_name = f"KDE{name}"
		kde_config = getattr(input, kde_config_name)()
		vector_shape_name = f"RenderShape{name}"
		vector_shape = getattr(input, vector_shape_name)()

		custom_colors_name = f"CustomColors{name}"
		custom_colors = getattr(input, custom_colors_name)()
		if len(custom_colors) == 0:
			Error("Error: Please specify at least one color.")
			return 
		elif len(custom_colors) < 2:
			custom_colors = custom_colors + custom_colors

		latitude = df[lat_col]
		longitude = df[lon_col]
		values = df[val_col]

		if disable:
			return
		
		# Calculate kernel density estimation (https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.gaussian_kde.html)
		if "Color by Density" in kde_config:
			stack = vstack([longitude, latitude])  # stack arrays vertically
			kde = gaussian_kde(stack)  # estimate PDF (probability density function)
			density = kde(stack)
			df[val_col] = values + density * 0.1
		
		# Define data point style
		if render == "Raster":
			FoliumHeatMap(list(zip(df[lat_col], df[lon_col], df[val_col])),
            min_opacity=opacity,
            max_zoom=0,
            radius=radius,
            blur=blur).add_to(map)
		elif render == "Vector":
            # Define a linear colormap
			colormap = LinearColormap(
				colors=custom_colors,
				vmin=df[val_col].min(),
				vmax=df[val_col].max()
			)
			# add a CircleMarker for each data point
			for index, row in df.iterrows():
				value = row[val_col]
				color = colormap(value)

				if vector_shape == "Circle":
					Circle(
						location=[row[lat_col], row[lon_col]],
						radius=radius * 10,
						color=color,
						fill=True,
						opacity=opacity,
						fill_opacity=opacity,
						stroke=False
					).add_to(map)
				else:
					lat, lon = row[lat_col], row[lon_col]
					rect_radius = radius / 10000
					Rectangle(
						bounds=[[lat - rect_radius, lon - rect_radius], [lat + rect_radius, lon + rect_radius]],
						color=color,
						fill=True,
						opacity=opacity,
						fill_opacity=opacity,
						stroke=False
					).add_to(map)
		map.fit_bounds(map.get_bounds())


	@output
	@render.data_frame
	def Table(): 
		df = DataChoropleth()
		if df is None or df.empty:
			return DataFrame({"Note": ["No data to display! Please upload your data or select an example data set in the sidebar."]})
		try:
			grid = render.DataGrid(df, editable=True)
			Valid.set(True)
			return grid
		except:
			return DataFrame({'Note': [ui.HTML('Could not render data! <br><br>Please make sure the names in your dataset match the names in a corresponding GeoJSON. Your input data should also have a column of values, and optionally, time. <br><br>See more formatting info <a href="https://github.com/WishartLab/heatmapper2/wiki/Format#geomap:~:text=work%20with%20it.-,Geomap,-Geomap%20has%20two"; target="_blank"; rel=”noopener noreferrer”>here</a>.')]})


	@Table.set_patch_fn
	def UpdateTable(*, patch: render.CellPatch) -> render.CellValue:
		if config.Type() == "Integer": value = int(patch["value"])
		elif config.Type() == "Float": value = float(patch["value"])
		else: value = patch["value"]
		DataCache.Invalidate(File(input))
		return value
	
	# Info text in welcome tab
	@render.ui
	def Welcome():
		return ui.HTML("""
			<h1>Maps</h1>
			Geomap displays values based on geographical boundaries, such as country, state, or province. Upload a data file and specify a GeoJSON in the sidebar to get started, or select 'Example' to check out a pre-loaded example. Navigate to the 'Heatmap' tab to see the heatmap, 'Table' to look at the input data, or 'GeoJSON' to see the geographical boundaries available in the currently selected GeoJSON file.
			
			<br><br>
			<img src="https://github.com/WishartLab/heatmapper2/wiki/assets/Geomap.png" alt="Geomap"; style="max-width:500px;">
				 
			<br><br><h3>Format</h3>
			Geomap requires a data file as well as a GeoJSON.
			<br>
			<i>Input data can be formatted as follows:</i>
				 <ul>
				 <li>A 'Name' column and 'Value' column(s), where names in the 'Name' column match available geographical boundaries in the currently selected GeoJSON. Select which value column to display using the 'Value' dropdown, if there is more than one.</li>
				 <li><u>Temporal format 1:</u> A 'Name' column, 'Value' column(s), and a 'Time' column. Rows will be grouped by time and plotted linearly. Names in the 'Name' column should match available geographical boundaries in the currently selected GeoJSON. Select which value column to display using the 'Value' dropdown, if there is more than one. (See Example 3)</li>
				 <li><u>Temporal format 2:</u> A 'Name' column, and multiple 'Time' columns, each containing the value of the associated name at that time (i.e. each row contains a name, and multiple values of that name at different time points). 'Time' column names are parsed such that all characters up to the first whitespace indicate the time (e.g. '1990 [emissions in kilotonnes]' becomes '1990'). See Example 2. </li>
				 </ul>
			<br>
			<i>Geomap heatmaps can be generated from the following file formats:</i>
			<table style="border-spacing: 100px";>
			<tr>
				<th>Table Files</th>
				<th>GeoJSON Files</th>
			</tr>
			<tr>
				<td style="padding-right:50px;">
					<li>.csv</li>
					<li>.dat</li>
					<li>.odf</li>
					<li>.tab</li>
					<li>.tsv</li>
					<li>.txt</li>
					<li>.xls</li>
					<li>.xlsx</li>
				</td>
				<td style="vertical-align:top;">
					<li>standard .geojson files, see <a href="https://geojson.org/"; target="_blank"; rel=”noopener noreferrer”>geojson.org</a></li>
				</td>
			</tr>
			</table>
				 
			<br><h3>Interface</h3>
			Remember to select or upload a GeoJSON with boundaries that match the names in your data.
		
			<br>Click on the '?' icon beside sidebar options to read more about them.
		""")


	def GenerateHeatmap():
		with ui.Progress() as p:

			p.inc(message="Loading input...")
			df_choropleth = GetDataChoropleth()
			df_coordinates = GetDataCoordinate()

			if df_choropleth is None and df_coordinates is None:
				print("No Choropleth/Coordinate dataframes :(")
				return ui.HTML("No data to display! Please upload your data or select an example data set in the sidebar.")
			
			###### set up background map ######
			p.inc(message="Loading GeoJSON...")
			try:
				geojson = JSON()
				# get available GeoJSON property names (e.g. name, id,...)
				properties = list(geojson['features'][0]['properties'].keys()) 
			except Exception:
				return ui.HTML('Make sure a GeoJSON is selected in the sidebar, <br>or upload your own following the <a href="https://geojson.org/"; target="_blank"; rel=”noopener noreferrer”>GeoJSON format</a>.')
			
			map_type = config.MapType()  # background map: either CartoDB or OSM

			# Give a placeholder map if nothing is selected, which should never really be the case.
			if df_choropleth.empty or geojson is None: return FoliumMap((53.5213, -113.5213), tiles=map_type, zoom_start=15)

			map = FoliumMap(tiles=map_type, zoom_control="topleft", zoom_start=2)

			###### create choropleth ######
			if df_choropleth is not None:
				p.inc(message="Formatting choropleth...")
				k_col, v_col, k_prop = config.KeyColumn(), config.ValueColumn(), config.KeyProperty()
				if k_col not in df_choropleth or v_col not in df_choropleth or k_prop not in properties: return ui.HTML("Data could not be displayed. <br>Please upload a Table file and a GeoJSON, or select an example data set in the sidebar. <br><br><i>Uploaded Table files should include: <br>a Key column (e.g. 'name', 'continent', 'country', 'location') <br>and a Value column (e.g. 'value', 'weight', 'intensity')</i>")

				p.inc(message="Dropping Invalid Values...")
				names = []
				for feature in geojson["features"]:  # for each defined territory/polygon
					names.append(feature["properties"][k_prop])  # collect the specified property (e.g. name)

				# remove high and low values if "range of interest" is enabled
				to_drop = []
				for index, key in zip(df_choropleth.index, df_choropleth[k_col]):
					if key not in names: to_drop.append(index)

				df_choropleth = df_choropleth.drop(to_drop)
				if len(df_choropleth) == 0:
					Error("No locations found")
					return ui.HTML("No locations were found. <br>Please ensure your data table contains a name column, whose values match a property in the GeoJSON.")

				# Load the choropleth onto the map
				p.inc(message="Plotting...")
				LoadChoropleth(df_choropleth, map, geojson, k_col, v_col, k_prop, p)

			###### create coordinate layers ######
			if df_coordinates is not None:
				# df_coordinate is dict  of {datapath: df}
				# get file names & datapaths from input.CoordinateUpload()
				datapath_to_name = {}
				for file in input.CoordinateUpload():
					datapath_to_name[file["datapath"]] = file["name"]
				for data in df_coordinates:
					# get filename so we can build ui element ids
					name = datapath_to_name[data].split(".")[0]
					# set up dataframe
					df_coord = df_coordinates[data]
					df_coord = df_coord.copy(deep=True)
					
					p.inc(message="Formatting...")
					lon_col = Filter(df_coord.columns, ColumnType.Longitude)
					lat_col = Filter(df_coord.columns, ColumnType.Latitude)
					if lat_col is None or lon_col is None: 
						return ui.HTML('The heat map could not be rendered. <br><br>Please ensure your input data contains a latitude column (named "latitude" or "lat"), and a longitude column (named "longitude", "long", or "lon"). Column names are case-insensitive. <br>More information on formatting is available in the <a href="https://github.com/WishartLab/heatmapper2/wiki/Format#geocoordinate:~:text=the%20Table%20tab)-,Geocoordinate,-Geocoordinate%20takes%20a"; target="_blank"; rel=”noopener noreferrer;>Wiki</a>.')
					val_col_name = f"ValueColumnCoord{name}"
					val_col = getattr(input, val_col_name)()
					# colour all points uniformly
					if val_col == "Uniform":
						df_coord["Default_Uniform_Values"] = [1] * len(df_coord[lat_col])
						val_col = "Default_Uniform_Values"

					# drop invalid values if using range of interest
					p.inc(message="Dropping Invalid Values...")
					roi_name = f"ROICoord{name}"
					roi = getattr(input, roi_name)()
					roi_mode_name = f"ROI_ModeCoord{name}"
					roi_mode = getattr(input, roi_mode_name)()
					min_name = f"Min{name}"
					min_val = getattr(input, min_name)()
					max_name = f"Max{name}"
					max_val = getattr(input, max_name)()

					if roi:
						to_drop = []
						lower, upper = min_val, max_val
						for index, value in zip(df_coord.index, df_coord[val_col]):
							if value < lower or value > upper:
								if roi_mode == "Remove": to_drop.append(index)
								elif roi_mode == "Round": df_coord.at[index, val_col] = upper if value > upper else lower
						df_coord = df_coord.drop(to_drop)
						if len(df_coord) == 0:
							Error("No locations to display! Check your Range of Interest and ensure the Value Column is properly set.")
							return ui.HTML("No locations to display! Check your Range of Interest and ensure the Value Column is properly set.")
					
					# time_col_name = f"TimeColumnCoord{name}"
					# time_col = getattr(input, time_col_name)()
					# generate coordinate map and add to map
					p.inc(message="Plotting...")
					GenerateCoordinateMap(df_coord, map, name, val_col, lat_col, lon_col)

			map.fit_bounds(map.get_bounds())
			return map

	@output
	@render.ui
	def Heatmap(): return GenerateHeatmap()


	@output
	@render.ui
	@reactive.event(input.Update)
	def HeatmapReactive(): return GenerateHeatmap()


	@output
	@render.data_frame
	def GeoJSON():
		geojson = JSON()
		if geojson is None:
			return DataFrame({'Note':[ui.HTML('Hmmm, we could not render the GeoJSON table.<br><br>Make sure a GeoJSON is selected in the right hand sidebar, <br>or upload your own following the <a href="https://geojson.org/"; target="_blank"; rel=”noopener noreferrer”>GeoJSON format</a>.')]})
		try:	
			# get selected key property (e.g. name) for each polygon/territory in GeoJSON
			names = [feature['properties'][config.KeyProperty()] for feature in geojson['features']]
			# return a simple dataframe of the kye properties to display under the GeoJSON tab
			return DataFrame({config.KeyProperty(): names})
		except:
			Error("Could not render the GeoJSON table!")
			return DataFrame({'Note':[ui.HTML('Hmmm, we could not render the GeoJSON table.<br><br>Make sure a GeoJSON is selected in the right hand sidebar, <br>or upload your own following the <a href="https://geojson.org/"; target="_blank"; rel=”noopener noreferrer”>GeoJSON format</a>.')]})


	@reactive.effect
	@reactive.event(input.ExampleInfo)
	def ExampleInfo():
		"""
		Display information text about each example file
		"""
		Msg(ui.HTML(InfoChoropleth[input.Example()]))
		

	@render.download(filename=lambda: f"table{config.TableType()}")
	def DownloadTable(): 
		data = GetDataChoropleth()
		
		# return error if no data to download
		if data.empty:
			Error("The downloaded table is empty! Please upload your data or select an example data set in the sidebar.")
		
		file_contents = data.to_string()
		yield file_contents


	@render.download(filename="heatmap.html")
	def DownloadHeatmap(): yield GenerateHeatmap().get_root().render()


app_ui = ui.page_fluid(

	ui.tags.style("""
		.navbar {
			position: fixed;  /* prevent navbar from scrolling */
			top: 0;
			height: 10vh;
			width: 100%;
			z-index: 1001;
			overflow-x: auto;
        }
		.navbar-nav {
			flex-wrap: nowrap !important;
		}
			   
		.bslib-sidebar-layout {
			margin-top: 10vh;  /* prevent content from being hidden under navbar */
		}
		.bslib-grid {
			display: flex;
			width: 100%;
		    justify-content: space-between;
		}

	"""),

	ui.panel_title(title=None, window_title="Geomap"),
	NavBar(),

	ui.layout_sidebar(
		# left hand sidebar - Coordinate settings
		ui.sidebar(

			ui.accordion(
				# file selection for choropleth
				ui.accordion_panel(
					"Select a Choropleth File",
					Inlineify(
						ui.input_select, 
						id="Example", 
						label=ui.input_action_link(id="ExampleInfo", label="File Info"), 
						choices={
							"Backyard_Hens_and_Bees.csv": "Hens & Bees (Edmonton)",
							"example6.csv": "COVID-19 (Canada)"
							},
						),
					
					ui.input_radio_buttons(id="JSONFile", label="Select a GeoJSON file", choices=["Provided", "Upload"], selected="Provided", inline=True),
					ui.panel_conditional(
						"input.JSONFile === 'Upload'",
						ui.input_file("JSONUpload", None, accept=[".geojson"], multiple=False),
					),
					ui.panel_conditional(
						"input.JSONFile === 'Provided'",
						ui.input_select(id="JSONSelection", label=None, choices=Mappings, multiple=False, selected="edmonton.geojson"),
					),
				),
				# file selection for latitude & longitude points
				ui.accordion_panel(
					"Select Coordinate Files",
					ui.input_radio_buttons(id="CoordinateFiles", label="Select coordinate files", choices=["Example", "Upload"], selected="Example", inline=True),
					ui.panel_conditional(
						"input.CoordinateFiles === 'Upload'",
						ui.input_file(
							"CoordinateUpload", 
							None, 
							accept=[".csv", ".txt", ".dat", ".tsv", ".tab", ".xlsx", ".xls", ".odf"], 
							multiple=True,
						),
					),
					ui.panel_conditional(
						"input.CoordinateFiles === 'Example'",
						ui.input_select(
							id="CoordinateSelection", 
							label=None, 
							choices={
								"gardens.csv": "Edmonton Community Gardens",
							}, 
							multiple=False, 
							selected="gardens.csv",
							),
					),
				)
			),
			
			TableOptions(config),

			ui.panel_conditional(
				"input.MainTab === 'HeatmapTab'",

				Update(),

				ui.accordion(
					ui.accordion_panel(
						"Choropleth Settings",
						ui.HTML("<b>Columns/Properties</b>"),
						config.KeyColumn.UI(ui.input_select, id="KeyColumn", label="Name Column", choices=[], tooltip="Specify a column in your data that contains location names. These location names must correspond to location names in the GeoJSON file. Click on the 'GeoJSON' tab in the main view area to see location names in the currently selected GeoJSON file."),
						config.ValueColumn.UI(ui.input_select, id="ValueColumn", label="Value Column", choices=[], tooltip="Specify a column containing the data to plot."),
						config.KeyProperty.UI(ui.input_select, id="KeyProperty", label="GeoJSON Property", choices=[], tooltip="Select a property in the GeoJSON file that corresponds to the location names in your data. Click on the 'GeoJSON' tab in the main view area to see available properties in the currently selected GeoJSON file."),
						
						ui.HTML("<b>Heatmap</b>"),
						config.MapType.UI(ui.input_select, id="MapType", label="Background Map", choices={"CartoDB Positron": "CartoDB", "OpenStreetMap": "OSM"}, tooltip="Specify the background map to plot your data on. CartoDB is a simpler map, while OSM is more highly annotated."),
						config.Opacity.UI(ui.input_slider, id="Opacity", label="Choropleth Opacity", min=0.0, max=1.0, step=0.1, tooltip="Specify the opacity of the heatmap. 1.0 indicates full opacity, while lower values make the background map more visible."),
					),

					# DO NOT CHANGE THIS ID - individual file settings panels are oriented based on this
					id="ChoroplethSettings",
				),
				ui.download_button(id="DownloadHeatmap", label="Download HTML"),
			),	
			position="left",
			padding="10px",
			gap="20px",
			width="300px",
		),
		MainTab(ui.nav_panel("GeoJSON", ui.output_data_frame("GeoJSON")), m_type=ui.output_ui),
		height="86vh",
		
	)
)

app = App(app_ui, server)























































