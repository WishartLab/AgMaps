# Ecodistrict source: https://sis.agr.gc.ca/cansis/publications/maps/eco/all/districts/index.html


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
from folium import Map as FoliumMap, Circle, GeoJson, Rectangle
from folium.features import GeoJsonTooltip, GeoJsonPopup
from folium.plugins import HeatMap as FoliumHeatMap
from geopandas import GeoDataFrame
from pandas import DataFrame
from branca.colormap import LinearColormap
from scipy.stats import gaussian_kde
from numpy import vstack
import re

from shared import Cache, Colors, Inlineify, NavBar, MainTab, Pyodide, Filter, ColumnType, TableOptions, Raw, InitializeConfig, ColorMaps, Error, Update, Msg, File
from geojson import Mappings

try:
	from user import config
except ImportError:
	from config import config

# Required for Shiny
import branca, certifi, xyzservices

URL = f"{Raw}/geomap/data/" if Pyodide else "../data/"

def server(input, output, session):
	new_load_flag = reactive.value(False)
	@reactive.effect
	def set_load_flag():
		new_load_flag.set(True)

	InfoChoropleth = {
		"Backyard_Hens_and_Bees.csv": '''<u>Input type:</u> .csv Data <br><u>Contents:</u> Number of properties with hens or bees in Edmonton, by neighbourhood.''',
		"FormerMunicipalities.csv": '''<u>Input type:</u> .csv Data<br> <u>Contents:</u> Former municipalities absorbed by the city of Edmonton. <br><u>Source:</u> <a href="https://en.wikipedia.org/wiki/List_of_neighbourhoods_in_Edmonton"; target="_blank">Wikipedia</a>''',
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


	def GetNameFromPath(filepath:str) -> str:
		"""
		@brief Given a filepath, extract the file name with no extension
		"""
		# TODO: user pathlib instead
		filename = filepath.split("/")[-1]
		return filename.split(".")[0]


	async def MakeColumnSelectors(name, key, val, choice):
		"""
		@brief Create ui elements to select a name and value column from choropleth data files
		"""
		try:
			# remove old ui elements and labels
			ui.remove_ui(selector=f"#{name}_column_select")
		except Exception as e:
			print(f"Error removing previous column selectors for {name}:\n{e}")

		key_ui = ui.input_select(
			id=f"KeyColumn{name}",
			label=f"Location Names", 
			choices=[key],
			#selected=key,
			)
		val_ui = ui.input_select(
			id=f"ValueColumn{name}", 
			label=f"Value Column", 
			choices=val,
			selected=val[choice],
			)
		accordion = ui.accordion(
				ui.accordion_panel(
					f"{name}",
					ui.input_checkbox_group(id=f"Disable{name}", inline=False, label=None, choices=["Disable Layer"], selected=None),
					key_ui,
					val_ui,
					# ui.input_slider(id=f"OpacityChoro{name}", label="Opacity", min=0.0, max=1.0, step=0.1),
				), 
				id=f"{name}_column_select"
			)
		ui.insert_ui(
			accordion,
			selector="#ChoroplethSettings",
			where="beforeEnd",
		)


	async def MakeColorSelectors(name, df, column_name):
		"""
		@info If data is categorical, for each category of choropleth data, create a colour selector ui element.
		Else if data is numerical, create a colour scheme selector
		@param filepath(str): used to uniqeuly identify ui elements
		@param df(pandas df): dataframe containing the value column
		@param column_name(str): the column with choropleth values
		"""
		try:
			# remove old colour dropdowns
			ui.remove_ui(selector=f"#{name}_colour_dropdowns")
		except Exception as e:
			print(f"Error removing previous colour selectors:\n{e}")

		# get all colors
		color_keys = list(Colors)
		color_index = 0
		
		# get unique values in value column
		values = df[column_name].unique()
		# are values numerical or categorical?
		data_type = GetChoroplethDataType(values)

		# user can select a colour for each category
		if data_type == "categorical":
			all_color_select = []
			for category in values:
				# remove invalid characters for id name
				category_clean = re.sub(r"\s+", "", str(category))
				category_clean = re.sub(f"[^a-zA-Z0-9]", "_", category)
				color_select = ui.input_select(id=f"{name}Select{category_clean}", label=f"{category}", choices=Colors, multiple=False, selectize=True, selected=color_keys[color_index])
				all_color_select.append(color_select)
				if color_index < (len(color_keys) - 1):
					color_index += 1

			accordion = ui.accordion(
				ui.accordion_panel(
					f"{name} Colors",
					*all_color_select,
				), 
				id=f"{name}_colour_dropdowns"
			)

		# user can select colours for a linear scheme
		else:
			accordion = ui.accordion(
				ui.accordion_panel(
					f"{name} Colors",
					ui.input_select(id=f"{name}ColorSelect", label=None, choices=Colors, multiple=True, selectize=True, selected=list(Colors)[0:3]),
				), 
				id=f"{name}_colour_dropdowns"
			)

		ui.insert_ui(
			accordion,
			selector="#ChoroplethSettings",
			where="beforeEnd",
		)


	@reactive.effect
	@reactive.event(input.Example, input.Reset, new_load_flag)
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
				example_file = input.Example(),
			)))
			Valid.set(False)

			data = DataChoropleth()  # dict of df
			
			for filepath in data:
				# make Key Column and Value Column ui elements
				df = data[filepath]
				name = GetNameFromPath(filepath)
				# get options for key and value column names
				columns = df.columns
				key = Filter(columns, ColumnType.Name, id=None)
				val = Filter(columns, ColumnType.Value, id=None, all=True)
				if val:
					choice = 0
					while choice < len(val) and val[choice] == key:
						choice += 1
				
				await MakeColumnSelectors(name, key, val, choice)

				# make colour selectors for each category in val column
				await MakeColorSelectors(name, df, val[choice])

			DataCache.Invalidate(File(input))
		except Exception as e:
			print(f"choropleth error: {e}")
			p.close()
			Error("File could not be loaded!\nChoropleth data can be uploaded as a .csv, .tsv, .txt, .xslx, .dat, .tab, or .odf file.")
			return


	@reactive.effect
	@reactive.event(input.JSONSelection)
	async def UpdateGeoJSON():
		"""
		Update data when the choropleth GeoJSON file is selected or modified.
		"""
		JSON.set(await DataCache.Load(
			input,
			source_file=None,
			example_file=input.JSONSelection(),
			source=URL,
			input_switch="Provided",
			example="Provided",
			default=None,
			p=ui.Progress(),
			p_name="GeoJSON"
		))
		full_geojson = JSON()
		filepath = next(iter(full_geojson))
		geojson = full_geojson[filepath]

		if geojson is None: return
		properties = list(geojson['features'][0]['properties'].keys())
		# update config.KeyProperty()
		Filter(properties, ColumnType.NameGeoJSON, id="KeyProperty")


	async def MakeSettingsForLayers(col_options:dict):
		"""
		@info When coordinate files are uploaded, make a settings dropdown for each file.
		"""
		if input.CoordinateSelection():
			try:
				# remove old layer settings dropdowns
				ui.remove_ui(selector="#dynamic_accordion")
			except Exception as e:
				print(f"Error removing previous file settings:\n{e}")
			
			settings_dropdowns = []
			for file in input.CoordinateSelection():
				#filename = file["name"]
				name = file.split(".")[0]
				dropdown = ui.accordion_panel(
					f"{file}", 
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
						ui.input_select(id=f"CustomColors{name}", label="Colors", choices=Colors, multiple=True, selectize=True, selected=["#8000ff","#ff0000","#ff9900","#fff200","#00ff80","#00bfff"]),
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
	@reactive.event(input.CoordinateSelection)
	async def UpdateCoordinateData():
		"""
		Update data when the coordinate data files are selected or modified.
		"""
		p = ui.Progress()

		if input.CoordinateSelection():
			col_options = {}
			try:
				DataCoordinate.set((await DataCache.Load(
					input, 
					source_file=None,
					example_file=input.CoordinateSelection(),
					input_switch="Example",
					example="Example",
					p=p,
					p_name="coordinate data"
					)
				))
				Valid.set(False)  # list of df?
				data = DataCoordinate()
				path_dict = {}
				for filepath in data:
					# TODO: use pathlib
					filename = filepath.split("/")[-1]
					path_dict[filename] = filepath

				for file in input.CoordinateSelection():
					# data[datapath]
					mapped_filepath = path_dict[file]
					file_df = data[mapped_filepath]

					name = file.split(".")[0]
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
				Error(ui.HTML("File could not be loaded!\nCoordinate data can be uploaded as a .csv, .tsv, .txt, .xslx, .dat, .tab, or .odf file.\n"), e)
				return


	def GetDataChoropleth(): return Table.data_view() if Valid() else DataChoropleth()

	# TODO: add table for geocoordinate file(s)
	def GetDataCoordinate(): 
		return DataCoordinate()  # dict of {datapath: df}


	def GetChoroplethDataType(data):
		"""
		@brief Determine if data is numerical or categorical
		@param categories(numpy.ndarray): a list of unique values
		Returns: str indicating "numerical" or "categorical"
		"""
		data_type = "numerical"
		for item in data:
			try:
				item = float(item)
			except:
				data_type = "categorical"
				return data_type
		return data_type 


	def LoadChoropleth(df, map, geojson, k_col, v_col, k_prop, name, p):
		"""
		@brief Applies a Choropleth to a Folium Map
		@param df: The DataFrame that contains information to plot
		@param map: The Folium map
		@param geojson: The geojson that contains territory info
		@param k_col: The name of the column within df that contains names
		@param v_col: the name of the column within df that contains the values to plot.
		@param k_prop: 
		@param name(str): name of the file that data was pulled from
		"""
		# check if layer is disabled
		disable_name = f"Disable{name}"
		disable = getattr(input, disable_name)()
		if disable:
			return

		# turn geojson dict into a df
		geojson_df = GeoDataFrame.from_features(geojson, crs="EPSG:4326")
		# merge with data df based on k_prop and k_col
		merged = geojson_df.merge(df, how="left", left_on=k_prop, right_on=k_col)

		opacity = config.Opacity()
		# opacity_name = f"OpacityChoro{name}"
		# opacity = getattr(input, opacity_name)()
		
		df_dict = df.set_index(k_col)[v_col]
		df_dict = df_dict.to_dict()

		# add a popup that appears on click
		popup = GeoJsonPopup(
			fields=[k_prop, v_col],
			aliases=["",""],
			localize=True,
			labels=True,
		)

		values = df[v_col].unique()
		data_type = GetChoroplethDataType(values)
		
		# if data is numerical, make continuous colormap
		if data_type == "numerical":
			vmin = min(values)
			vmax = max(values)
			selected_colors_num_ui = f"{name}ColorSelect"
			selected_colors_num = getattr(input, selected_colors_num_ui)()
			if len(selected_colors_num) < 2:
				selected_colors_num += selected_colors_num
			colormap = LinearColormap(selected_colors_num, vmin=vmin, vmax=vmax)
			GeoJson(
				merged,
				style_function=lambda x:
				{
					"fillColor": colormap(df_dict[x['properties']['name']]) if x['properties']['name'] in df_dict else "transparent",
					"color": "black",
					"weight": 0.5,
					"fillOpacity": opacity,
				},
				#tooltip=tooltip,
				popup=popup,
			).add_to(map)
		
		# if data is categorical, make categorical colormap
		else:
			colors = []
			indices = []
			index = 0
			category_to_color = {}

			# if categorical data, map categories to indices
			for category in values:
				category_name = re.sub(r"\s+", "", str(category))
				category_name = re.sub(f"[^a-zA-Z0-9]", "_", category)
				selected_color_ui = f"{name}Select{category_name}"
				selected_color = getattr(input, selected_color_ui)()

				colors.append(selected_color)
				category_to_color[category] = selected_color
				indices.append(index)
				index += 1

			GeoJson(
				merged,
				style_function=lambda x:
				{
					"fillColor": category_to_color[(df_dict[x['properties']['name']])] if x['properties']['name'] in df_dict else "transparent",
					"color": "black",
					"weight": 0.5,
					"fillOpacity": opacity,
				},
				#tooltip=tooltip,
				popup=popup,
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
		#TODO: modify for multiple choropleths! Currently just displays first in list
		data = DataChoropleth()
		filepath = next(iter(data))
		df = data[filepath]
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
			SOIL-HUB Maps display values based on geographical boundaries (such as country, state, or province), with coordinate points displayed on top. Select a GeoJSON and choropleth data in the sidebar to get started. Select optional coordinate points to display on top. <br>Navigate to the 'Heatmap' tab to see the heatmap, 'Table' to look at the choropleth data, or 'GeoJSON' to see the geographical boundaries available in the currently selected GeoJSON file.
			
			<br><br>
			<img src="https://github.com/WishartLab/heatmapper2/wiki/assets/Geomap.png" alt="Geomap"; style="max-width:500px;">
			<br>  
 
			<br><h3>Troubleshooting</h3>  
			Remember to select a GeoJSON with boundaries that match the names in your data. <br>Navigate to the 'GeoJSON' tab to see available boundaries in the GeoJSON file. Navigate to the 'Table' tab to see if your rows match these boundaries.
		
			<br>Click on the '?' icon beside sidebar options to read more about them.
		""")


	def GenerateHeatmap():
		with ui.Progress() as p:

			p.inc(message="Loading input...")
			#df_choropleth = GetDataChoropleth()
			df_choropleth = DataChoropleth()
			df_coordinates = GetDataCoordinate()

			if df_choropleth is None and df_coordinates is None:
				print("No Choropleth/Coordinate dataframes :(")
				return ui.HTML("No data to display! Please upload your data or select an example data set in the sidebar.")
			
			###### set up background map ######
			p.inc(message="Loading GeoJSON...")
			try:
				# get available GeoJSON property names (e.g. name, id,...)
				geojson_dict = JSON()
				filepath = next(iter(geojson_dict))
				geojson = geojson_dict[filepath]
				properties = list(geojson['features'][0]['properties'].keys()) 
			except Exception:
				return ui.HTML('Make sure a GeoJSON is selected in the sidebar, <br>or upload your own following the <a href="https://geojson.org/"; target="_blank"; rel=”noopener noreferrer”>GeoJSON format</a>.')
			
			map_type = config.MapType()  # background map: either CartoDB or OSM
			if map_type == "Esri World Imagery":
				map_type = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
				attribution = ('Powered by <a href="https://www.esri.com">Esri</a>. Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community')
			elif map_type == "OpenStreetMap":
				attribution = ('&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors')
			elif map_type == "CartoDB Positron":
				attribution = ('&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
				   'contributors, &copy; <a href="https://carto.com/attribution">CARTO</a>')

			# Give a placeholder map if nothing is selected, which should never really be the case.
			if df_choropleth is None or geojson is None: return FoliumMap((53.5213, -113.5213), tiles=map_type, attr=attribution, zoom_start=15)

			map = FoliumMap(tiles=map_type, attr=attribution, zoom_control="topleft", zoom_start=2)

			###### create choropleths ######
			if df_choropleth is not None:
				p.inc(message="Formatting choropleth...")
				k_prop = config.KeyProperty()
				
				# df_choropleth is dict of choropleth dfs indexed by filepath, parse each one
				for df_filepath in df_choropleth:
					name_choro = GetNameFromPath(df_filepath)
					df_choro = df_choropleth[df_filepath]
					# HERE iiiiiiiiiiii
					v_col_name = f"ValueColumn{name_choro}"
					v_col = getattr(input, v_col_name)()
					k_col_name = f"KeyColumn{name_choro}"
					k_col = getattr(input, k_col_name)()
					if k_col not in df_choro or v_col not in df_choro or k_prop not in properties: 
						return ui.HTML("Data could not be displayed. <br>Please upload a Table file and a GeoJSON, or select an example data set in the sidebar. <br><br><i>Uploaded Table files should include: <br>a Key column (e.g. 'name', 'continent', 'country', 'location') <br>and a Value column (e.g. 'value', 'weight', 'intensity')</i>")

					p.inc(message="Dropping Invalid Values...")
					names = []
					for feature in geojson["features"]:  # for each defined territory/polygon
						names.append(feature["properties"][k_prop])  # collect the specified property (e.g. name)

					# remove high and low values if "range of interest" is enabled
					to_drop = []
					for index, key in zip(df_choro.index, df_choro[k_col]):
						if key not in names: to_drop.append(index)

					df_choro = df_choro.drop(to_drop)
					if len(df_choro) == 0:
						Error("No locations found")
						return ui.HTML("No locations were found. <br>Please ensure your data table contains a name column, whose values match a property in the GeoJSON.")

					# Load the choropleth onto the map
					p.inc(message="Plotting...")
					LoadChoropleth(df_choro, map, geojson, k_col, v_col, k_prop, name_choro, p)

			###### create coordinate layers ######
			if df_coordinates is not None:
				for filepath in df_coordinates:

					# get filename so we can build ui element ids
					name = GetNameFromPath(filepath)
					# set up dataframe
					df_coord = df_coordinates[filepath]
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
		geojson_dict = JSON()
		filepath = next(iter(geojson_dict))
		geojson = geojson_dict[filepath]

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
					"Choropleth Files",
					ui.HTML("Select a GeoJSON file"),
					ui.input_select(id="JSONSelection", label=None, choices=Mappings, multiple=False, selected="edmonton.geojson"),
					
					ui.HTML("Add choropleth data"),
					Inlineify(
						ui.input_select, 
						id="Example", 
						label=ui.input_action_link(id="ExampleInfo", label="File Info"), 
						choices={
							"Backyard_Hens_and_Bees.csv": "Hens & Bees (Edmonton)",
							"FormerMunicipalities.csv": "Former Municipalities (Edmonton)"
							},
						multiple=True,
						selected="Backyard_Hens_and_Bees.csv",
						selectize=True,
						),
				),
				# file selection for latitude & longitude points
				ui.accordion_panel(
					"Coordinate Files",
					ui.input_select(
						id="CoordinateSelection", 
						label=None, 
						choices={
							"gardens.csv": "Edmonton Community Gardens",
							"foodbanks.csv": "Edmonton Food Banks",
						}, 
						multiple=True, 
						selected=None,
						selectize=True,
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
						# config.KeyColumn.UI(ui.input_select, id="KeyColumn", label="Name Column", choices=[], tooltip="Specify a column in your data that contains location names. These location names must correspond to location names in the GeoJSON file. Click on the 'GeoJSON' tab in the main view area to see location names in the currently selected GeoJSON file."),
						# config.ValueColumn.UI(ui.input_select, id="ValueColumn", label="Value Column", choices=[], tooltip="Specify a column containing the data to plot."),
						config.KeyProperty.UI(ui.input_select, id="KeyProperty", label="GeoJSON Property", choices=[], tooltip="Select a property in the GeoJSON file that corresponds to the location names in your data. Click on the 'GeoJSON' tab in the main view area to see available properties in the currently selected GeoJSON file."),
						
						ui.HTML("<b>Heatmap</b>"),
						config.MapType.UI(ui.input_select, id="MapType", label="Background Map", choices={"CartoDB Positron": "Simple Map", "OpenStreetMap": "Street Map", "Esri World Imagery": "Satellite"}, tooltip="Specify the background map to plot your data on. CartoDB is a simpler map, while OSM is more highly annotated."),
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
