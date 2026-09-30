import { CommonModule } from '@angular/common';
import { ChangeDetectorRef, Component, inject, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { MapComponent, PlanOverlay } from './map/map';
import { EmergencyDataService, GOVERNORATES, Hospital, Shelter } from './services/emergency-data.service';
import { BarChartComponent, BarChartItem } from './bar-chart/bar-chart';
import { Config } from './services/config';

import { ChatComponent } from './chat/chat';

type ShelterSortField = 'name' | 'classes' | 'governorate';
type HospitalSortField = 'name' | 'governorate' | 'ownership';
type SortDirection = 'ASC' | 'DESC';

/** Raw spellings of each governorate in the two layers (they differ), for map filter SQL. */
const RAW_GOVERNORATES: Record<string, { schools: string[]; hospitals: string[] }> = {
  Nabatieh: { schools: ['Nabatiye'], hospitals: ['El Nabatieh'] },
  'Baalbek-Hermel': { schools: ['Baalbek-Hermel'], hospitals: ['Baalbeck-Hermel'] },
};

function sqlString(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

function compare(a: string | number | null, b: string | number | null): number {
  if (a === b) return 0;
  if (a === null) return 1; // missing values last
  if (b === null) return -1;
  return typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b));
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, MapComponent, BarChartComponent, ChatComponent],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App implements OnInit {
  @ViewChild(MapComponent) private mapComponent?: MapComponent;

  // ===================== DATA =====================
  shelters: Shelter[] = [];
  hospitals: Hospital[] = [];
  selectedId: string | null = null;
  dataLoading = true;
  dataError = false;

  readonly governorates = GOVERNORATES;

  // ===================== UI =====================
  filtersOpen = true;
  sidebarOpen = true;
  chatOpen = false;

  showSheltersByGovernorate = false;
  showHospitalsByGovernorate = false;
  showHospitalServices = false;
  showShelterRecords = false;
  showHospitalRecords = false;

  // ===================== FILTERS =====================
  searchText = '';
  governorate: string | null = null;
  ownership: string | null = null;
  bloodBankOnly = false;
  radiologyOnly = false;

  // ===================== TABLES =====================
  pageSizeOptions = [10, 50, 100];

  shelterSortOptions: { value: ShelterSortField; label: string }[] = [
    { value: 'name', label: 'Name' },
    { value: 'classes', label: 'Class sections' },
    { value: 'governorate', label: 'Governorate' },
  ];
  shelterSortField: ShelterSortField | null = null;
  shelterSortDirection: SortDirection = 'ASC';
  shelterPageSize = 50;
  shelterPage = 1;

  hospitalSortOptions: { value: HospitalSortField; label: string }[] = [
    { value: 'name', label: 'Name' },
    { value: 'governorate', label: 'Governorate' },
    { value: 'ownership', label: 'Ownership' },
  ];
  hospitalSortField: HospitalSortField | null = null;
  hospitalSortDirection: SortDirection = 'ASC';
  hospitalPageSize = 50;
  hospitalPage = 1;

  private readonly configService = inject(Config);

  constructor(
    private readonly dataService: EmergencyDataService,
    private readonly cdr: ChangeDetectorRef
  ) {}

  async ngOnInit(): Promise<void> {
    await this.configService.loadConfigurations();
    try {
      // Both layers are small (~600 shelters, ~145 hospitals), so load them once
      // and filter/sort/page in the browser.
      [this.shelters, this.hospitals] = await Promise.all([
        this.dataService.getShelters(),
        this.dataService.getHospitals(),
      ]);
    } catch (error) {
      console.error('Loading facilities failed', error);
      this.dataError = true;
    }
    this.dataLoading = false;
    this.cdr.detectChanges();
  }

  toggleSidebar(): void {
    this.sidebarOpen = !this.sidebarOpen;
  }

  toggleFilters(): void {
    this.filtersOpen = !this.filtersOpen;
  }

  openChat(): void {
    this.chatOpen = true;
  }

  closeChat(): void {
    this.chatOpen = false;
  }

  /** Called when the assistant returns a plan (see ChatComponent's `showPlan` output). */
  async showPlanOnMap(plan: PlanOverlay): Promise<void> {
    this.focusMapView();
    await this.mapComponent?.showPlan(plan);
  }

  // ===================== FILTERING =====================
  get filteredShelters(): Shelter[] {
    const text = this.searchText.toLowerCase().trim();
    return this.shelters.filter(
      (s) =>
        (!this.governorate || s.governorate === this.governorate) &&
        (!text || [s.name, s.nameAr, s.cadastral, s.caza].some((v) => v?.toLowerCase().includes(text)))
    );
  }

  get filteredHospitals(): Hospital[] {
    const text = this.searchText.toLowerCase().trim();
    return this.hospitals.filter(
      (h) =>
        (!this.governorate || h.governorate === this.governorate) &&
        (!this.ownership || h.ownership === this.ownership) &&
        (!this.bloodBankOnly || h.bloodBank === true) &&
        (!this.radiologyOnly || h.radiology === true) &&
        (!text || [h.name, h.nameAr, h.district].some((v) => v?.toLowerCase().includes(text)))
    );
  }

  clearFilters(): void {
    this.searchText = '';
    this.governorate = null;
    this.ownership = null;
    this.bloodBankOnly = false;
    this.radiologyOnly = false;
    this.onFiltersChanged();
  }

  onFiltersChanged(): void {
    this.shelterPage = 1;
    this.hospitalPage = 1;
    this.mapComponent?.setLayerFilters(this.shelterWhereClause(), this.hospitalWhereClause());
  }

  private shelterWhereClause(): string {
    const clauses: string[] = [];
    if (this.governorate) {
      const values = RAW_GOVERNORATES[this.governorate]?.schools ?? [this.governorate];
      clauses.push(`Governorate IN (${values.map(sqlString).join(',')})`);
    }
    const text = this.searchText.trim();
    if (text) {
      clauses.push(`(School_Name LIKE ${sqlString(`%${text}%`)} OR Cadastral LIKE ${sqlString(`%${text}%`)} OR Caza LIKE ${sqlString(`%${text}%`)})`);
    }
    return clauses.length ? clauses.join(' AND ') : '1=1';
  }

  private hospitalWhereClause(): string {
    const clauses: string[] = [];
    if (this.governorate) {
      const values = RAW_GOVERNORATES[this.governorate]?.hospitals ?? [this.governorate];
      clauses.push(`governorat IN (${values.map(sqlString).join(',')})`);
    }
    if (this.ownership) clauses.push(`ownership = ${sqlString(this.ownership)}`);
    if (this.bloodBankOnly) clauses.push(`Blood_bank = 'Yes'`);
    if (this.radiologyOnly) clauses.push(`Radiology = 'Yes'`);
    const text = this.searchText.trim();
    if (text) {
      clauses.push(`(Facility_E LIKE ${sqlString(`%${text}%`)} OR district LIKE ${sqlString(`%${text}%`)})`);
    }
    return clauses.length ? clauses.join(' AND ') : '1=1';
  }

  // ===================== STATISTICS =====================
  get publicHospitalCount(): number {
    return this.filteredHospitals.filter((h) => h.ownership === 'Public').length;
  }

  get bloodBankCount(): number {
    return this.filteredHospitals.filter((h) => h.bloodBank).length;
  }

  get sheltersByGovernorate(): BarChartItem[] {
    return this.countByGovernorate(this.filteredShelters);
  }

  get hospitalsByGovernorate(): BarChartItem[] {
    return this.countByGovernorate(this.filteredHospitals);
  }

  get hospitalServices(): BarChartItem[] {
    const hospitals = this.filteredHospitals;
    return [
      { label: 'Radiology', value: hospitals.filter((h) => h.radiology).length },
      { label: 'Lab', value: hospitals.filter((h) => h.lab).length },
      { label: 'Blood bank', value: hospitals.filter((h) => h.bloodBank).length },
      { label: 'Public', value: hospitals.filter((h) => h.ownership === 'Public').length },
    ];
  }

  private countByGovernorate(items: { governorate: string | null }[]): BarChartItem[] {
    return this.governorates
      .map((g) => ({ label: g, value: items.filter((i) => i.governorate === g).length }))
      .filter((item) => item.value > 0);
  }

  // ===================== SHELTER RECORDS =====================
  get sortedShelters(): Shelter[] {
    const field = this.shelterSortField;
    if (!field) return this.filteredShelters;
    const dir = this.shelterSortDirection === 'ASC' ? 1 : -1;
    return [...this.filteredShelters].sort((a, b) => dir * compare(a[field], b[field]));
  }

  get shelterTotalPages(): number {
    return Math.max(1, Math.ceil(this.filteredShelters.length / this.shelterPageSize));
  }

  get pagedShelters(): Shelter[] {
    const start = (this.shelterPage - 1) * this.shelterPageSize;
    return this.sortedShelters.slice(start, start + this.shelterPageSize);
  }

  setShelterSortField(field: string): void {
    this.shelterSortField = (field as ShelterSortField) || null;
    this.shelterPage = 1;
  }

  toggleShelterSortDirection(): void {
    this.shelterSortDirection = this.shelterSortDirection === 'ASC' ? 'DESC' : 'ASC';
    this.shelterPage = 1;
  }

  changeShelterPageSize(size: number): void {
    this.shelterPageSize = size;
    this.shelterPage = 1;
  }

  goToShelterPage(page: number): void {
    this.shelterPage = Math.min(Math.max(page, 1), this.shelterTotalPages);
  }

  // ===================== HOSPITAL RECORDS =====================
  get sortedHospitals(): Hospital[] {
    const field = this.hospitalSortField;
    if (!field) return this.filteredHospitals;
    const dir = this.hospitalSortDirection === 'ASC' ? 1 : -1;
    return [...this.filteredHospitals].sort((a, b) => dir * compare(a[field], b[field]));
  }

  get hospitalTotalPages(): number {
    return Math.max(1, Math.ceil(this.filteredHospitals.length / this.hospitalPageSize));
  }

  get pagedHospitals(): Hospital[] {
    const start = (this.hospitalPage - 1) * this.hospitalPageSize;
    return this.sortedHospitals.slice(start, start + this.hospitalPageSize);
  }

  setHospitalSortField(field: string): void {
    this.hospitalSortField = (field as HospitalSortField) || null;
    this.hospitalPage = 1;
  }

  toggleHospitalSortDirection(): void {
    this.hospitalSortDirection = this.hospitalSortDirection === 'ASC' ? 'DESC' : 'ASC';
    this.hospitalPage = 1;
  }

  changeHospitalPageSize(size: number): void {
    this.hospitalPageSize = size;
    this.hospitalPage = 1;
  }

  goToHospitalPage(page: number): void {
    this.hospitalPage = Math.min(Math.max(page, 1), this.hospitalTotalPages);
  }

  // ===================== SELECTION + ZOOM =====================
  async zoomToShelter(shelter: Shelter): Promise<void> {
    this.selectedId = `s${shelter.id}`;
    this.focusMapView();
    await this.mapComponent?.zoomToFacility('shelter', shelter.id);
  }

  async zoomToHospital(hospital: Hospital): Promise<void> {
    this.selectedId = `h${hospital.id}`;
    this.focusMapView();
    await this.mapComponent?.zoomToFacility('hospital', hospital.id);
  }

  yesNo(value: boolean | null): string {
    return value === true ? 'Yes' : value === false ? 'No' : '—';
  }

  /**
   * Switches the sidebar to the map section and scrolls it into view, so zooms
   * triggered from the tables or the chat are immediately visible.
   */
  private focusMapView(): void {
    this.setActiveSection('scene');
    document.getElementById('scene')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // ===================== SIDEBAR ACTIVE SECTION =====================
  activeSection: string = 'overview';

  setActiveSection(section: string): void {
    this.activeSection = section;
  }
}
