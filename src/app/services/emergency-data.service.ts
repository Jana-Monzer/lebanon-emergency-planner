import { inject, Injectable } from '@angular/core';

import FeatureLayer from '@arcgis/core/layers/FeatureLayer';
import Query from '@arcgis/core/rest/support/Query';
import { Config } from './config';

/**
 * Field names verified against the live layers (see emergency-planner/README.md):
 * - Schools: the shelter flag is the Arabic field مركز_ايواء ('Yes' | ''); وضع_المدرسة is the
 *   school status (' ', 'مقفلة' = closed, 'موجودة مرتين' = duplicate record, excluded).
 * - Hospital_Labs mixes hospitals and labs; hospitals have Facility_T = 'Hospitals , مستشفيات'.
 */
const SHELTER_FIELD = 'مركز_ايواء';
const STATUS_FIELD = 'وضع_المدرسة';
const PHONE_FIELD = 'رقم_الهاتف';
export const SHELTER_WHERE = `${SHELTER_FIELD} = 'Yes' AND (${STATUS_FIELD} IS NULL OR ${STATUS_FIELD} <> 'موجودة مرتين')`;
export const HOSPITAL_WHERE = `Facility_T = 'Hospitals , مستشفيات'`;

/** The two layers spell governorates differently; this maps both to one display name. */
const GOVERNORATE_ALIASES: Record<string, string> = {
  Nabatiye: 'Nabatieh',
  'El Nabatieh': 'Nabatieh',
  'Baalbeck-Hermel': 'Baalbek-Hermel',
};

export const GOVERNORATES = ['Akkar', 'Baalbek-Hermel', 'Beirut', 'Bekaa', 'Mount Lebanon', 'Nabatieh', 'North', 'South'];

export interface Shelter {
  id: number;
  name: string;
  nameAr: string | null;
  cadastral: string | null;
  caza: string | null;
  governorate: string | null;
  classes: number | null;
  phone: string | null;
  closed: boolean;
  lat: number;
  lon: number;
}

export interface Hospital {
  id: number;
  name: string;
  nameAr: string | null;
  district: string | null;
  governorate: string | null;
  ownership: string | null;
  bloodBank: boolean | null;
  radiology: boolean | null;
  lab: boolean | null;
  phone: string | null;
  lat: number;
  lon: number;
}

function clean(value: unknown): string | null {
  const text = value == null ? '' : String(value).trim();
  return text || null;
}

function yesNo(value: unknown): boolean | null {
  const text = (clean(value) ?? '').toLowerCase();
  return text === 'yes' ? true : text === 'no' ? false : null;
}

export function normalizeGovernorate(value: unknown): string | null {
  const text = clean(value);
  if (!text || text === 'NA') return null;
  return GOVERNORATE_ALIASES[text] ?? text;
}

@Injectable({ providedIn: 'root' })
export class EmergencyDataService {
  private readonly configService = inject(Config);

  // Cached so the map and the dashboard share one loaded instance of each layer.
  private sheltersLayer?: FeatureLayer;
  private schoolsLayer?: FeatureLayer;
  private hospitalsLayer?: FeatureLayer;

  getSheltersLayer(): FeatureLayer {
    this.sheltersLayer ??= new FeatureLayer({
      url: this.configService.configurations.schoolsLayerUrl,
      title: 'Shelter schools',
      definitionExpression: SHELTER_WHERE,
      outFields: ['*'],
      popupTemplate: {
        title: '{School_Name}',
        content: [
          {
            type: 'fields',
            fieldInfos: [
              { fieldName: 'School_Name___Arabic', label: 'Arabic name' },
              { fieldName: 'Cadastral', label: 'Area' },
              { fieldName: 'Caza', label: 'District' },
              { fieldName: 'Governorate', label: 'Governorate' },
              { fieldName: 'Total_Nb_Class', label: 'Class sections (2022-23)' },
              { fieldName: PHONE_FIELD, label: 'Phone' },
            ],
          },
        ],
      },
    });
    return this.sheltersLayer;
  }

  /** Every school, including ones not designated as shelters (hidden by default on the map). */
  getSchoolsLayer(): FeatureLayer {
    this.schoolsLayer ??= new FeatureLayer({
      url: this.configService.configurations.schoolsLayerUrl,
      title: 'All public schools',
      definitionExpression: `${STATUS_FIELD} IS NULL OR ${STATUS_FIELD} <> 'موجودة مرتين'`,
      outFields: ['*'],
      visible: false,
      popupTemplate: {
        title: '{School_Name}',
        content: [
          {
            type: 'fields',
            fieldInfos: [
              { fieldName: 'Cadastral', label: 'Area' },
              { fieldName: 'Caza', label: 'District' },
              { fieldName: SHELTER_FIELD, label: 'Designated shelter' },
            ],
          },
        ],
      },
    });
    return this.schoolsLayer;
  }

  getHospitalsLayer(): FeatureLayer {
    this.hospitalsLayer ??= new FeatureLayer({
      url: this.configService.configurations.hospitalsLayerUrl,
      title: 'Hospitals',
      definitionExpression: HOSPITAL_WHERE,
      outFields: ['*'],
      popupTemplate: {
        title: '{Facility_E}',
        content: [
          {
            type: 'fields',
            fieldInfos: [
              { fieldName: 'Facility_A', label: 'Arabic name' },
              { fieldName: 'ownership', label: 'Ownership' },
              { fieldName: 'district', label: 'District' },
              { fieldName: 'Blood_bank', label: 'Blood bank' },
              { fieldName: 'Radiology', label: 'Radiology' },
              { fieldName: 'Lab', label: 'Lab' },
              { fieldName: 'Phone', label: 'Phone' },
            ],
          },
        ],
      },
    });
    return this.hospitalsLayer;
  }

  /** All shelter schools (~600 - one request, well under the layer's 2000-record limit). */
  async getShelters(): Promise<Shelter[]> {
    const features = await this.queryAll(this.getSheltersLayer(), SHELTER_WHERE);
    return features.map(({ attributes: a, geometry: g }: any) => ({
      id: a.OBJECTID,
      name: clean(a.School_Name) ?? clean(a.School_Name___Arabic) ?? 'Unnamed school',
      nameAr: clean(a.School_Name___Arabic),
      cadastral: clean(a.Cadastral),
      caza: clean(a.Caza),
      governorate: normalizeGovernorate(a.Governorate),
      classes: a.Total_Nb_Class ?? null,
      phone: clean(a[PHONE_FIELD]),
      closed: clean(a[STATUS_FIELD]) === 'مقفلة',
      lat: g?.latitude ?? a.Latitude,
      lon: g?.longitude ?? a.longitude,
    }));
  }

  /** All hospitals (~145). */
  async getHospitals(): Promise<Hospital[]> {
    const features = await this.queryAll(this.getHospitalsLayer(), HOSPITAL_WHERE);
    return features.map(({ attributes: a, geometry: g }: any) => ({
      id: a.FID,
      name: clean(a.Facility_E) ?? clean(a.Facility_A) ?? 'Unnamed hospital',
      nameAr: clean(a.Facility_A),
      district: clean(a.district),
      governorate: normalizeGovernorate(a.governorat),
      ownership: clean(a.ownership),
      bloodBank: yesNo(a.Blood_bank),
      radiology: yesNo(a.Radiology),
      lab: yesNo(a.Lab),
      phone: clean(a.Phone),
      lat: g?.latitude,
      lon: g?.longitude,
    }));
  }

  private async queryAll(layer: FeatureLayer, where: string) {
    await layer.load();
    const query = new Query({ where, outFields: ['*'], returnGeometry: true, outSpatialReference: { wkid: 4326 } });
    const result = await layer.queryFeatures(query);
    return result.features;
  }
}
