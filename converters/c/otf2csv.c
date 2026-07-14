/*
 * otf2csv.c — convert an OTF2 trace to CSV using the OTF2 C library.
 *
 * Part of fastotf2-bench: the C baseline for the converter comparison.
 * Emits, per the FastOTF2Converter schema:
 *   <outputDir>/<Group>_<Thread>_callgraph.csv
 *     columns: Thread,Group,Depth,Name,Start Time,End Time,Duration
 *   <outputDir>/<Group>_metrics.csv
 *     columns: Group,Metric Name,Time,Value
 *
 * CSV only — Parquet output would require the Apache Arrow C++/GLib toolchain
 * and is intentionally out of scope for the C baseline.
 *
 * Build (against the OTF2 install in the container):
 *   gcc -O3 -o otf2csv otf2csv.c $(otf2-config --cflags) \
 *       $(otf2-config --libs) $(otf2-config --ldflags)
 *
 * Usage:
 *   otf2csv <trace.otf2> [--outputDir DIR] [--keep-dups] [--read-only]
 */
#include <otf2/otf2.h>
#include <stdlib.h>
#include <stdio.h>
#include <inttypes.h>
#include <string.h>
#include <stdbool.h>
#include <time.h>
#include <sys/stat.h>
#include <sys/types.h>

/* --- Config Options --- */
static bool  g_dedup = true;      /* skip consecutive duplicate metric values */
static bool  g_read_only = false; /* read/parse only, do not write output     */
static char  g_output_dir[1024] = "."; /* directory for output files          */

/* --- Helper Structs --- */
typedef struct {
    uint64_t timerResolution;
    uint64_t globalOffset;
} ClockProperties;

typedef struct {
    uint64_t start_time;
    char*    region_name;
} StackFrame;

typedef struct {
    OTF2_MetricMemberRef member_id;
    OTF2_Type            type;
    OTF2_MetricValue     value;
} MetricLastValue;

typedef struct {
    OTF2_LocationRef      id;
    char*                 name;
    char*                 group_name;
    OTF2_LocationGroupRef group_id;
    FILE*                 csv_cg;
    StackFrame*           stack;
    int                   stack_ptr;
    int                   stack_capacity;
} LocationState;

typedef struct {
    OTF2_LocationGroupRef id;
    char*                 name;
    FILE*                 csv_met;
    MetricLastValue*      metric_cache;
    size_t                n_metric_cache;
    size_t                cap_metric_cache;
} GroupState;

typedef struct { OTF2_StringRef id; char* str; } StringEntry;
typedef struct { OTF2_RegionRef id; char* name; } RegionEntry;
typedef struct { OTF2_MetricMemberRef id; char* name; char* unit; } MetricMemberEntry;
typedef struct { OTF2_MetricRef id; OTF2_MetricMemberRef member_id; } MetricClassEntry;
typedef struct { OTF2_MetricRef id; OTF2_MetricRef class_id; } MetricInstanceEntry;

typedef struct {
    ClockProperties clock;
    StringEntry*  strings;  size_t n_strings;  size_t cap_strings;
    RegionEntry*  regions;  size_t n_regions;  size_t cap_regions;
    MetricMemberEntry* members; size_t n_members; size_t cap_members;
    MetricClassEntry*  mclasses; size_t n_mclasses; size_t cap_mclasses;
    MetricInstanceEntry* minst; size_t n_minst; size_t cap_minst;
    GroupState*    groups;    size_t n_groups;    size_t cap_groups;
    LocationState* locations; size_t n_locations; size_t cap_locations;
} AppContext;

/* --- Utils --- */
static double get_time_seconds(uint64_t timestamp, ClockProperties* clk) {
    if (clk->timerResolution == 0) return 0.0;
    return (double)(timestamp - clk->globalOffset) / (double)clk->timerResolution;
}

static char* get_string(AppContext* ctx, OTF2_StringRef ref) {
    for (size_t i = 0; i < ctx->n_strings; i++)
        if (ctx->strings[i].id == ref) return ctx->strings[i].str;
    return "Unknown";
}

static char* get_region_name(AppContext* ctx, OTF2_RegionRef ref) {
    for (size_t i = 0; i < ctx->n_regions; i++)
        if (ctx->regions[i].id == ref) return ctx->regions[i].name;
    return "UnknownRegion";
}

static GroupState* get_group_state(AppContext* ctx, OTF2_LocationGroupRef ref) {
    for (size_t i = 0; i < ctx->n_groups; i++)
        if (ctx->groups[i].id == ref) return &ctx->groups[i];
    return NULL;
}

static LocationState* get_location_state(AppContext* ctx, OTF2_LocationRef ref) {
    for (size_t i = 0; i < ctx->n_locations; i++)
        if (ctx->locations[i].id == ref) return &ctx->locations[i];
    return NULL;
}

/* --- Metric deduplication --- */
static bool values_equal(OTF2_Type type, OTF2_MetricValue v1, OTF2_MetricValue v2) {
    if (type == OTF2_TYPE_INT64)  return v1.signed_int == v2.signed_int;
    if (type == OTF2_TYPE_UINT64) return v1.unsigned_int == v2.unsigned_int;
    if (type == OTF2_TYPE_DOUBLE) return v1.floating_point == v2.floating_point;
    return false;
}

/* Returns true if this sample should be SKIPPED (duplicate of the last value). */
static bool check_and_update_metric_cache(GroupState* grp, OTF2_MetricMemberRef member_id,
                                          OTF2_Type type, OTF2_MetricValue val) {
    if (!g_dedup) return false;
    for (size_t i = 0; i < grp->n_metric_cache; i++) {
        if (grp->metric_cache[i].member_id == member_id) {
            if (values_equal(type, grp->metric_cache[i].value, val)) return true;
            grp->metric_cache[i].value = val;
            return false;
        }
    }
    if (grp->n_metric_cache == grp->cap_metric_cache) {
        grp->cap_metric_cache = (grp->cap_metric_cache == 0) ? 8 : grp->cap_metric_cache * 2;
        grp->metric_cache = realloc(grp->metric_cache, grp->cap_metric_cache * sizeof(MetricLastValue));
    }
    MetricLastValue* entry = &grp->metric_cache[grp->n_metric_cache++];
    entry->member_id = member_id;
    entry->type = type;
    entry->value = val;
    return false;
}

/* --- Definition callbacks --- */
static OTF2_CallbackCode CbClockProps(void* userData, uint64_t timerResolution,
                                      uint64_t globalOffset, uint64_t traceLength,
                                      uint64_t realtimeTimestamp) {
    AppContext* ctx = (AppContext*)userData;
    ctx->clock.timerResolution = timerResolution;
    ctx->clock.globalOffset = globalOffset;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode CbString(void* userData, OTF2_StringRef self, const char* string) {
    AppContext* ctx = (AppContext*)userData;
    if (ctx->n_strings == ctx->cap_strings) {
        ctx->cap_strings = (ctx->cap_strings == 0) ? 128 : ctx->cap_strings * 2;
        ctx->strings = realloc(ctx->strings, ctx->cap_strings * sizeof(StringEntry));
    }
    ctx->strings[ctx->n_strings].id = self;
    ctx->strings[ctx->n_strings].str = strdup(string);
    ctx->n_strings++;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode CbLocationGroup(void* userData, OTF2_LocationGroupRef self,
                                         OTF2_StringRef name, OTF2_LocationGroupType type,
                                         OTF2_SystemTreeNodeRef parent,
                                         OTF2_LocationGroupRef creating) {
    AppContext* ctx = (AppContext*)userData;
    if (ctx->n_groups == ctx->cap_groups) {
        ctx->cap_groups = (ctx->cap_groups == 0) ? 16 : ctx->cap_groups * 2;
        ctx->groups = realloc(ctx->groups, ctx->cap_groups * sizeof(GroupState));
    }
    GroupState* g = &ctx->groups[ctx->n_groups++];
    g->id = self;
    g->name = strdup(get_string(ctx, name));
    g->csv_met = NULL;
    g->metric_cache = NULL; g->n_metric_cache = 0; g->cap_metric_cache = 0;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode CbLocation(void* userData, OTF2_LocationRef self,
                                    OTF2_StringRef name, OTF2_LocationType type,
                                    uint64_t numEvents, OTF2_LocationGroupRef group) {
    AppContext* ctx = (AppContext*)userData;
    if (ctx->n_locations == ctx->cap_locations) {
        ctx->cap_locations = (ctx->cap_locations == 0) ? 16 : ctx->cap_locations * 2;
        ctx->locations = realloc(ctx->locations, ctx->cap_locations * sizeof(LocationState));
    }
    LocationState* l = &ctx->locations[ctx->n_locations++];
    l->id = self;
    l->name = strdup(get_string(ctx, name));
    l->group_id = group;
    l->group_name = NULL;
    l->csv_cg = NULL;
    l->stack = malloc(1024 * sizeof(StackFrame));
    l->stack_capacity = 1024;
    l->stack_ptr = 0;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode CbRegion(void* userData, OTF2_RegionRef self, OTF2_StringRef name,
                                  OTF2_StringRef cname, OTF2_StringRef desc,
                                  OTF2_RegionRole role, OTF2_Paradigm par,
                                  OTF2_RegionFlag flags, OTF2_StringRef file,
                                  uint32_t begin, uint32_t end) {
    AppContext* ctx = (AppContext*)userData;
    if (ctx->n_regions == ctx->cap_regions) {
        ctx->cap_regions = (ctx->cap_regions == 0) ? 128 : ctx->cap_regions * 2;
        ctx->regions = realloc(ctx->regions, ctx->cap_regions * sizeof(RegionEntry));
    }
    ctx->regions[ctx->n_regions].id = self;
    ctx->regions[ctx->n_regions].name = strdup(get_string(ctx, name));
    ctx->n_regions++;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode CbMetricMember(void* userData, OTF2_MetricMemberRef self,
                                        OTF2_StringRef name, OTF2_StringRef desc,
                                        OTF2_MetricType mtype, OTF2_MetricMode mode,
                                        OTF2_Type vtype, OTF2_Base base, int64_t exp,
                                        OTF2_StringRef unit) {
    AppContext* ctx = (AppContext*)userData;
    if (ctx->n_members == ctx->cap_members) {
        ctx->cap_members = (ctx->cap_members == 0) ? 16 : ctx->cap_members * 2;
        ctx->members = realloc(ctx->members, ctx->cap_members * sizeof(MetricMemberEntry));
    }
    ctx->members[ctx->n_members].id = self;
    ctx->members[ctx->n_members].name = strdup(get_string(ctx, name));
    ctx->members[ctx->n_members].unit = strdup(get_string(ctx, unit));
    ctx->n_members++;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode CbMetricClass(void* userData, OTF2_MetricRef self, uint8_t nMetrics,
                                       const OTF2_MetricMemberRef* members,
                                       OTF2_MetricOccurrence occ, OTF2_RecorderKind kind) {
    AppContext* ctx = (AppContext*)userData;
    if (ctx->n_mclasses == ctx->cap_mclasses) {
        ctx->cap_mclasses = (ctx->cap_mclasses == 0) ? 16 : ctx->cap_mclasses * 2;
        ctx->mclasses = realloc(ctx->mclasses, ctx->cap_mclasses * sizeof(MetricClassEntry));
    }
    ctx->mclasses[ctx->n_mclasses].id = self;
    ctx->mclasses[ctx->n_mclasses].member_id = (nMetrics > 0) ? members[0] : 0;
    ctx->n_mclasses++;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode CbMetricInstance(void* userData, OTF2_MetricRef self,
                                          OTF2_MetricRef metricClass, OTF2_LocationRef recorder,
                                          OTF2_MetricScope scope, uint64_t sc) {
    AppContext* ctx = (AppContext*)userData;
    if (ctx->n_minst == ctx->cap_minst) {
        ctx->cap_minst = (ctx->cap_minst == 0) ? 16 : ctx->cap_minst * 2;
        ctx->minst = realloc(ctx->minst, ctx->cap_minst * sizeof(MetricInstanceEntry));
    }
    ctx->minst[ctx->n_minst].id = self;
    ctx->minst[ctx->n_minst].class_id = metricClass;
    ctx->n_minst++;
    return OTF2_CALLBACK_SUCCESS;
}

/* --- Event callbacks --- */
static OTF2_CallbackCode EvEnter(OTF2_LocationRef location, OTF2_TimeStamp time, void* userData,
                                 OTF2_AttributeList* attributes, OTF2_RegionRef region) {
    AppContext* ctx = (AppContext*)userData;
    LocationState* loc = get_location_state(ctx, location);
    if (!loc) return OTF2_CALLBACK_SUCCESS;
    if (loc->stack_ptr == loc->stack_capacity) {
        loc->stack_capacity *= 2;
        loc->stack = realloc(loc->stack, loc->stack_capacity * sizeof(StackFrame));
    }
    loc->stack[loc->stack_ptr].start_time = time;
    loc->stack[loc->stack_ptr].region_name = get_region_name(ctx, region);
    loc->stack_ptr++;
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode EvLeave(OTF2_LocationRef location, OTF2_TimeStamp time, void* userData,
                                 OTF2_AttributeList* attributes, OTF2_RegionRef region) {
    AppContext* ctx = (AppContext*)userData;
    LocationState* loc = get_location_state(ctx, location);
    if (!loc || loc->stack_ptr == 0) return OTF2_CALLBACK_SUCCESS;
    loc->stack_ptr--;
    StackFrame frame = loc->stack[loc->stack_ptr];
    double start = get_time_seconds(frame.start_time, &ctx->clock);
    double end   = get_time_seconds(time, &ctx->clock);
    if (g_read_only) return OTF2_CALLBACK_SUCCESS;
    if (loc->csv_cg) {
        fprintf(loc->csv_cg, "%s,%s,%d,\"%s\",%.9f,%.9f,%.9f\n",
                loc->name, loc->group_name, loc->stack_ptr, frame.region_name,
                start, end, end - start);
    }
    return OTF2_CALLBACK_SUCCESS;
}

static OTF2_CallbackCode EvMetric(OTF2_LocationRef location, OTF2_TimeStamp time, void* userData,
                                  OTF2_AttributeList* atts, OTF2_MetricRef metric,
                                  uint8_t nMetrics, const OTF2_Type* types,
                                  const OTF2_MetricValue* values) {
    AppContext* ctx = (AppContext*)userData;
    LocationState* loc = get_location_state(ctx, location);
    if (!loc) return OTF2_CALLBACK_SUCCESS;
    GroupState* grp = get_group_state(ctx, loc->group_id);
    if (!grp || !grp->csv_met) return OTF2_CALLBACK_SUCCESS;

    OTF2_MetricRef classRef = metric;
    for (size_t i = 0; i < ctx->n_minst; i++)
        if (ctx->minst[i].id == metric) { classRef = ctx->minst[i].class_id; break; }

    OTF2_MetricMemberRef memberID = 0;
    for (size_t i = 0; i < ctx->n_mclasses; i++)
        if (ctx->mclasses[i].id == classRef) { memberID = ctx->mclasses[i].member_id; break; }

    if (check_and_update_metric_cache(grp, memberID, types[0], values[0]))
        return OTF2_CALLBACK_SUCCESS;
    if (g_read_only) return OTF2_CALLBACK_SUCCESS;

    char* mName = "UnknownMetric";
    for (size_t j = 0; j < ctx->n_members; j++)
        if (ctx->members[j].id == memberID) { mName = ctx->members[j].name; break; }

    double t = get_time_seconds(time, &ctx->clock);
    if (types[0] == OTF2_TYPE_INT64)
        fprintf(grp->csv_met, "%s,%s,%.9f,%" PRId64 "\n", grp->name, mName, t, values[0].signed_int);
    else if (types[0] == OTF2_TYPE_UINT64)
        fprintf(grp->csv_met, "%s,%s,%.9f,%" PRIu64 "\n", grp->name, mName, t, values[0].unsigned_int);
    else if (types[0] == OTF2_TYPE_DOUBLE)
        fprintf(grp->csv_met, "%s,%s,%.9f,%.9f\n", grp->name, mName, t, values[0].floating_point);
    return OTF2_CALLBACK_SUCCESS;
}

static void usage(const char* prog) {
    printf("Usage: %s <trace.otf2> [--outputDir DIR] [--keep-dups] [--read-only]\n", prog);
    printf("  --outputDir DIR   Directory for output CSV files (default: current dir)\n");
    printf("  --keep-dups       Do not skip consecutive duplicate metric values\n");
    printf("  --read-only       Read/parse the trace but write no output\n");
}

int main(int argc, char** argv) {
    if (argc < 2) { usage(argv[0]); return 1; }
    const char* tracePath = argv[1];

    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--keep-dups") == 0) {
            g_dedup = false;
        } else if (strcmp(argv[i], "--read-only") == 0) {
            g_read_only = true;
        } else if (strcmp(argv[i], "--outputDir") == 0 && i + 1 < argc) {
            strncpy(g_output_dir, argv[++i], sizeof(g_output_dir) - 1);
            g_output_dir[sizeof(g_output_dir) - 1] = 0;
        } else if (strncmp(argv[i], "--outputDir=", 12) == 0) {
            strncpy(g_output_dir, argv[i] + 12, sizeof(g_output_dir) - 1);
            g_output_dir[sizeof(g_output_dir) - 1] = 0;
        } else {
            fprintf(stderr, "Unknown argument: %s\n", argv[i]);
            usage(argv[0]);
            return 1;
        }
    }

    if (!g_read_only) mkdir(g_output_dir, 0755); /* ignore EEXIST */
    if (g_dedup)     printf("Option enabled: skipping consecutive duplicate metric values.\n");
    if (g_read_only) printf("Option enabled: read-only mode (no output).\n");
    printf("Output directory: %s\n", g_output_dir);

    struct timespec ts_start, ts_end;
    clock_gettime(CLOCK_MONOTONIC, &ts_start);

    AppContext ctx = {0};
    OTF2_Reader* reader = OTF2_Reader_Open(tracePath);
    if (!reader) { fprintf(stderr, "Failed to open trace: %s\n", tracePath); return 1; }
    OTF2_Reader_SetSerialCollectiveCallbacks(reader);

    OTF2_GlobalDefReader* gdr = OTF2_Reader_GetGlobalDefReader(reader);
    OTF2_GlobalDefReaderCallbacks* gdcb = OTF2_GlobalDefReaderCallbacks_New();
    OTF2_GlobalDefReaderCallbacks_SetClockPropertiesCallback(gdcb, &CbClockProps);
    OTF2_GlobalDefReaderCallbacks_SetStringCallback(gdcb, &CbString);
    OTF2_GlobalDefReaderCallbacks_SetLocationGroupCallback(gdcb, &CbLocationGroup);
    OTF2_GlobalDefReaderCallbacks_SetLocationCallback(gdcb, &CbLocation);
    OTF2_GlobalDefReaderCallbacks_SetRegionCallback(gdcb, &CbRegion);
    OTF2_GlobalDefReaderCallbacks_SetMetricMemberCallback(gdcb, &CbMetricMember);
    OTF2_GlobalDefReaderCallbacks_SetMetricClassCallback(gdcb, &CbMetricClass);
    OTF2_GlobalDefReaderCallbacks_SetMetricInstanceCallback(gdcb, &CbMetricInstance);
    OTF2_Reader_RegisterGlobalDefCallbacks(reader, gdr, gdcb, &ctx);
    OTF2_GlobalDefReaderCallbacks_Delete(gdcb);
    uint64_t dummy;
    OTF2_Reader_ReadAllGlobalDefinitions(reader, gdr, &dummy);

    /* Open per-group metric files. */
    for (size_t i = 0; i < ctx.n_groups; i++) {
        if (g_read_only) continue;
        char filename[2048];
        snprintf(filename, sizeof(filename), "%s/%s_metrics.csv", g_output_dir, ctx.groups[i].name);
        ctx.groups[i].csv_met = fopen(filename, "w");
        if (ctx.groups[i].csv_met)
            fprintf(ctx.groups[i].csv_met, "Group,Metric Name,Time,Value\n");
    }
    /* Open per-location callgraph files. */
    for (size_t i = 0; i < ctx.n_locations; i++) {
        LocationState* loc = &ctx.locations[i];
        GroupState* grp = get_group_state(&ctx, loc->group_id);
        loc->group_name = grp ? grp->name : "Unknown";
        OTF2_Reader_SelectLocation(reader, loc->id);
        if (g_read_only) continue;
        char cleanL[512];
        strncpy(cleanL, loc->name, sizeof(cleanL) - 1); cleanL[sizeof(cleanL) - 1] = 0;
        for (int c = 0; cleanL[c]; c++) if (cleanL[c] == ' ') cleanL[c] = '_';
        char filename[2048];
        snprintf(filename, sizeof(filename), "%s/%s_%s_callgraph.csv",
                 g_output_dir, loc->group_name, cleanL);
        loc->csv_cg = fopen(filename, "w");
        if (loc->csv_cg)
            fprintf(loc->csv_cg, "Thread,Group,Depth,Name,Start Time,End Time,Duration\n");
    }

    OTF2_Reader_OpenDefFiles(reader);
    OTF2_Reader_OpenEvtFiles(reader);
    for (size_t i = 0; i < ctx.n_locations; i++) {
        OTF2_DefReader* dr = OTF2_Reader_GetDefReader(reader, ctx.locations[i].id);
        if (dr) {
            OTF2_Reader_ReadAllLocalDefinitions(reader, dr, &dummy);
            OTF2_Reader_CloseDefReader(reader, dr);
        }
        OTF2_Reader_GetEvtReader(reader, ctx.locations[i].id);
    }
    OTF2_Reader_CloseDefFiles(reader);

    printf("Processing events...\n");
    OTF2_GlobalEvtReader* ger = OTF2_Reader_GetGlobalEvtReader(reader);
    OTF2_GlobalEvtReaderCallbacks* ecb = OTF2_GlobalEvtReaderCallbacks_New();
    OTF2_GlobalEvtReaderCallbacks_SetEnterCallback(ecb, &EvEnter);
    OTF2_GlobalEvtReaderCallbacks_SetLeaveCallback(ecb, &EvLeave);
    OTF2_GlobalEvtReaderCallbacks_SetMetricCallback(ecb, &EvMetric);
    OTF2_Reader_RegisterGlobalEvtCallbacks(reader, ger, ecb, &ctx);
    OTF2_GlobalEvtReaderCallbacks_Delete(ecb);
    OTF2_Reader_ReadAllGlobalEvents(reader, ger, &dummy);

    for (size_t i = 0; i < ctx.n_groups; i++) {
        if (ctx.groups[i].csv_met) fclose(ctx.groups[i].csv_met);
        free(ctx.groups[i].metric_cache);
    }
    for (size_t i = 0; i < ctx.n_locations; i++) {
        if (ctx.locations[i].csv_cg) fclose(ctx.locations[i].csv_cg);
        free(ctx.locations[i].stack);
    }
    OTF2_Reader_CloseGlobalEvtReader(reader, ger);
    OTF2_Reader_CloseEvtFiles(reader);
    OTF2_Reader_Close(reader);

    clock_gettime(CLOCK_MONOTONIC, &ts_end);
    double elapsed = (ts_end.tv_sec - ts_start.tv_sec)
                   + (ts_end.tv_nsec - ts_start.tv_nsec) / 1e9;
    if (g_read_only) printf("Reading completed in %.3f seconds.\n", elapsed);
    else             printf("CSV conversion completed in %.3f seconds.\n", elapsed);
    return 0;
}
