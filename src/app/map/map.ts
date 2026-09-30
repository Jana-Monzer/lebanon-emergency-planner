import { AfterViewInit, ChangeDetectorRef, Component, ElementRef, OnDestroy, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import Map from '@arcgis/core/Map';
import SceneView from '@arcgis/core/views/SceneView';
import type MapView from '@arcgis/core/views/MapView';

import Basemap from '@arcgis/core/Basemap';
import TileLayer from '@arcgis/core/layers/TileLayer';
import FeatureLayer from '@arcgis/core/layers/FeatureLayer';
import GraphicsLayer from '@arcgis/core/layers/GraphicsLayer';

import Graphic from '@arcgis/core/Graphic';
import Point from '@arcgis/core/geometry/Point';
import Extent from '@arcgis/core/geometry/Extent';
import Polyline from '@arcgis/core/geometry/Polyline';

import Search from '@arcgis/core/widgets/Search';
import Home from '@arcgis/core/widgets/Home';
import Locate from '@arcgis/core/widgets/Locate';
import ScaleBar from '@arcgis/core/widgets/ScaleBar';
import LayerList from '@arcgis/core/widgets/LayerList';
import Legend from '@arcgis/core/widgets/Legend';
import BasemapGallery from '@arcgis/core/widgets/BasemapGallery';
import Expand from '@arcgis/core/widgets/Expand';

import { EmergencyDataService, HOSPITAL_WHERE, SHELTER_WHERE } from '../services/emergency-data.service';

export type FacilityKind = 'shelter' | 'hospital';

export interface PlanPoint {
  name: string;
  lat: number;
  lon: number;
  distance_km?: number | null;
}

/** What the map needs to draw an emergency plan returned by the agents. */
export interface PlanOverlay {
  location: PlanPoint;
  shelter?: PlanPoint | null;
  hospital?: PlanPoint | null;
}

const SHELTER_COLOR = [34, 139, 94];
const HOSPITAL_COLOR = [214, 48, 49];
const LOCATION_COLOR = [24, 118, 150];

const LEBANON_CAMERA = {
  position: { longitude: 35.6, latitude: 32.55, z: 150000 },
  heading: 15,
  tilt: 45,
};

@Component({
  selector: 'app-map',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './map.html',
  styleUrl: './map.css',
})
export class MapComponent implements AfterViewInit, OnDestroy {
  @ViewChild('mapContainer', { static: true })
  private mapContainer!: ElementRef<HTMLDivElement>;

  @ViewChild('latitudeInput')
  private latitudeInput!: ElementRef<HTMLInputElement>;

  @ViewChild('longitudeInput')
  private longitudeInput!: ElementRef<HTMLInputElement>;

  private view: SceneView | null = null;
  private sheltersLayer!: FeatureLayer;
  private hospitalsLayer!: FeatureLayer;
  private readonly planLayer = new GraphicsLayer({ title: 'Emergency plan', listMode: 'hide' });
  private readonly ready: Promise<void>;
  private markReady!: () => void;

  showCoordinates = false;
  coordinateError = false;
  facilityName = '';
  facilityNotFound = false;

  constructor(
    private readonly dataService: EmergencyDataService,
    private readonly cdr: ChangeDetectorRef,
  ) {
    this.ready = new Promise((resolve) => (this.markReady = resolve));
  }

  async ngAfterViewInit(): Promise<void> {
    const imageryBasemap = new Basemap({
      baseLayers: [
        new TileLayer({
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer',
        }),
      ],
    });

    const map = new Map({
      basemap: imageryBasemap,
      ground: 'world-elevation',
    });

    this.view = new SceneView({
      container: this.mapContainer.nativeElement,
      map,
      camera: LEBANON_CAMERA,
    });

    this.view.when(async () => {
      try {
        this.sheltersLayer = this.dataService.getSheltersLayer();
        this.hospitalsLayer = this.dataService.getHospitalsLayer();
        const schoolsLayer = this.dataService.getSchoolsLayer();

        await Promise.all([this.sheltersLayer.load(), this.hospitalsLayer.load(), schoolsLayer.load()]);

        schoolsLayer.renderer = this.markerRenderer([150, 150, 150], 6, 'circle');
        this.sheltersLayer.renderer = this.markerRenderer(SHELTER_COLOR, 9, 'square');
        this.hospitalsLayer.renderer = this.markerRenderer(HOSPITAL_COLOR, 11, 'cross');

        // Points sit on the terrain instead of floating at z=0 under mountains.
        for (const layer of [schoolsLayer, this.sheltersLayer, this.hospitalsLayer]) {
          layer.elevationInfo = { mode: 'on-the-ground' } as any;
        }

        map.addMany([schoolsLayer, this.sheltersLayer, this.hospitalsLayer, this.planLayer]);

        this.addWidgets(this.view!);
        this.markReady();
      } catch (error) {
        console.error('Layer loading failed:', error);
      }
    });
  }

  private markerRenderer(color: number[], size: number, style: string): any {
    return {
      type: 'simple',
      symbol: {
        type: 'simple-marker',
        style,
        size,
        color,
        // A 'cross' marker is drawn only with its outline, so that carries the colour.
        outline: style === 'cross' ? { color, width: 4 } : { color: [255, 255, 255], width: 1 },
      },
    };
  }

  /** Filters both facility layers on the map (the dashboard filters use the same governorate list). */
  setLayerFilters(shelterWhere: string, hospitalWhere: string): void {
    if (this.sheltersLayer) this.sheltersLayer.definitionExpression = `(${SHELTER_WHERE}) AND (${shelterWhere})`;
    if (this.hospitalsLayer) this.hospitalsLayer.definitionExpression = `(${HOSPITAL_WHERE}) AND (${hospitalWhere})`;
  }

  private addWidgets(view: SceneView): void {
    // Search Widget (OpenStreetMap Nominatim, limited to Lebanon)
    view.ui.add(
      new Search({
        view,
        includeDefaultSources: false,
        sources: [
          {
            placeholder: 'Search a place in Lebanon',
            getSuggestions: async (params: any) => {
              const term = params.suggestTerm ?? '';
              if (!term) {
                return [];
              }

              const results = await this.nominatim(term);
              return results.map((r: any) => ({
                key: 'nominatim',
                text: r.display_name,
                sourceIndex: params.sourceIndex,
              }));
            },
            getResults: async (params: any) => {
              const term = params.suggestResult?.text ?? params.searchTerm ?? '';
              const results = await this.nominatim(term);

              return results.map((r: any) => {
                const longitude = Number(r.lon);
                const latitude = Number(r.lat);

                return {
                  name: r.display_name,
                  extent: new Extent({
                    xmin: longitude - 0.03,
                    ymin: latitude - 0.03,
                    xmax: longitude + 0.03,
                    ymax: latitude + 0.03,
                    spatialReference: { wkid: 4326 },
                  }),
                  feature: new Graphic({ geometry: new Point({ longitude, latitude }) }),
                };
              });
            },
          } as any,
        ],
      }),
      'top-right',
    );

    // Home + Locate
    view.ui.add(
      [new Home({ view: view as unknown as MapView }), new Locate({ view: view as unknown as MapView })],
      'top-left',
    );

    // Scale Bar
    view.ui.add(new ScaleBar({ view: view as unknown as MapView, unit: 'metric' }), 'bottom-left');

    // Layer List
    view.ui.add(
      new Expand({ view, content: new LayerList({ view: view as unknown as MapView }), expandTooltip: 'Layers' }),
      'top-right',
    );

    // Legend
    view.ui.add(
      new Expand({ view, content: new Legend({ view: view as unknown as MapView }), expandTooltip: 'Legend' }),
      'top-right',
    );

    // Basemap Gallery
    view.ui.add(
      new Expand({ view, content: new BasemapGallery({ view: view as unknown as MapView }), expandTooltip: 'Basemap' }),
      'top-right',
    );
  }

  private async nominatim(term: string): Promise<any[]> {
    const response = await fetch(
      `https://nominatim.openstreetmap.org/search?format=json&countrycodes=lb&limit=5&q=${encodeURIComponent(term)}`,
    );
    return response.json();
  }

  goToCoordinates(latitude: number, longitude: number): void {
    this.view?.goTo({ center: [longitude, latitude], zoom: 15, tilt: 45 });
  }

  searchCoordinates(): void {
    if (!this.latitudeInput || !this.longitudeInput) {
      this.coordinateError = true;
      return;
    }

    const latitude = Number(this.latitudeInput.nativeElement.value);
    const longitude = Number(this.longitudeInput.nativeElement.value);

    const valid =
      Number.isFinite(latitude) &&
      Number.isFinite(longitude) &&
      latitude >= -90 &&
      latitude <= 90 &&
      longitude >= -180 &&
      longitude <= 180;

    if (!valid) {
      this.coordinateError = true;
      return;
    }

    this.coordinateError = false;
    this.goToCoordinates(latitude, longitude);
  }

  /**
   * Flies to a shelter (OBJECTID) or hospital (FID) and opens its popup.
   * Returns whether the feature was found. Used by the record tables.
   */
  async zoomToFacility(kind: FacilityKind, id: number): Promise<boolean> {
    await this.ready;
    const layer = kind === 'shelter' ? this.sheltersLayer : this.hospitalsLayer;
    const idField = kind === 'shelter' ? 'OBJECTID' : 'FID';
    return this.zoomToFirst(layer, `${idField} = ${id}`);
  }

  /** Searches shelters then hospitals by (English or Arabic) name. */
  async searchFacilityByName(): Promise<void> {
    const term = this.facilityName.trim().replace(/'/g, "''");
    if (!term) {
      return;
    }
    await this.ready;

    const found =
      (await this.zoomToFirst(this.sheltersLayer, `School_Name LIKE '%${term}%' OR School_Name___Arabic LIKE '%${term}%'`)) ||
      (await this.zoomToFirst(this.hospitalsLayer, `Facility_E LIKE '%${term}%' OR Facility_A LIKE '%${term}%'`));

    // ArcGIS's async work runs outside Angular's zone, so force change detection
    // for the "not found" popup.
    this.facilityNotFound = !found;
    this.cdr.detectChanges();
  }

  private async zoomToFirst(layer: FeatureLayer, where: string): Promise<boolean> {
    if (!this.view) {
      return false;
    }
    const query = layer.createQuery();
    query.where = `(${layer.definitionExpression}) AND (${where})`;
    query.outFields = ['*'];
    query.returnGeometry = true;

    const result = await layer.queryFeatures(query);
    const feature = result.features[0];
    if (!feature?.geometry) {
      return false;
    }

    await this.view.goTo({ target: feature.geometry, zoom: 16, tilt: 45 });
    this.view.openPopup({ features: [feature], location: feature.geometry as Point });
    return true;
  }

  /** Draws the user's location, the chosen shelter/hospital and straight-line connectors. */
  async showPlan(plan: PlanOverlay): Promise<void> {
    await this.ready;
    this.planLayer.removeAll();

    const origin = this.point(plan.location);
    const targets: [PlanPoint | null | undefined, number[], string][] = [
      [plan.shelter, SHELTER_COLOR, 'Nearest shelter'],
      [plan.hospital, HOSPITAL_COLOR, 'Nearest hospital'],
    ];

    for (const [target, color, label] of targets) {
      if (!target) continue;
      this.planLayer.add(
        new Graphic({
          geometry: new Polyline({
            paths: [[[plan.location.lon, plan.location.lat], [target.lon, target.lat]]],
            spatialReference: { wkid: 4326 },
          }),
          symbol: { type: 'simple-line', color, width: 3, style: 'short-dash' } as any,
        }),
      );
      this.planLayer.add(this.pin(target, color, `${label}: ${target.name}`, target.distance_km));
    }
    this.planLayer.add(this.pin(plan.location, LOCATION_COLOR, `Your location: ${plan.location.name}`));

    const points = [plan.location, plan.shelter, plan.hospital].filter((p): p is PlanPoint => !!p);
    const pad = 0.01;
    await this.view?.goTo({
      target: new Extent({
        xmin: Math.min(...points.map((p) => p.lon)) - pad,
        ymin: Math.min(...points.map((p) => p.lat)) - pad,
        xmax: Math.max(...points.map((p) => p.lon)) + pad,
        ymax: Math.max(...points.map((p) => p.lat)) + pad,
        spatialReference: { wkid: 4326 },
      }),
      tilt: 40,
    });
    this.view?.openPopup({ features: [this.planLayer.graphics.at(-1)!], location: origin });
  }

  clearPlan(): void {
    this.planLayer.removeAll();
  }

  private point(p: PlanPoint): Point {
    return new Point({ longitude: p.lon, latitude: p.lat });
  }

  private pin(p: PlanPoint, color: number[], title: string, distanceKm?: number | null): Graphic {
    return new Graphic({
      geometry: this.point(p),
      symbol: {
        type: 'simple-marker',
        style: 'circle',
        size: 18,
        color,
        outline: { color: [255, 255, 255], width: 3 },
      } as any,
      attributes: {
        title,
        detail: distanceKm != null ? `${distanceKm} km away (straight line)` : `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`,
      },
      popupTemplate: { title: '{title}', content: '{detail}' },
    });
  }

  ngOnDestroy(): void {
    if (this.view) {
      this.view.destroy();
      this.view = null;
    }
  }
}
