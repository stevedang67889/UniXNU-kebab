/*
 * Kebab bring-up stubs.
 *
 * The UniXNU XNU patches reference device probe entry points that only exist
 * when the matching native driver archive is linked.  The kebab kernel does not
 * build those drivers yet, so this archive provides weak no-op definitions.
 * Weak symbols let a real driver override them later.
 */
#define KSTUB __attribute__((weak))

KSTUB void native_adreno_firmware_probe(void) {}
KSTUB void native_constants_probe(void) {}
KSTUB void native_legacy_display_late_probe(void) {}
KSTUB void native_legacy_display_probe(void) {}
KSTUB void native_mobile_display_provider_probe(void) {}
KSTUB void native_mobile_graphics_client_probe(void) {}
KSTUB void native_offline_gpu_publish(void) {}
KSTUB void native_verified_root_probe(void) {}

KSTUB void native_owner_root_accepted_checkpoint(int a, int b) { (void)a; (void)b; }
KSTUB void native_owner_root_checkpoint(int a, int b, int c) { (void)a; (void)b; (void)c; }
KSTUB int  native_owner_publish_boot_manifest(int a, const char *b, int c, const char *d)
{ (void)a; (void)b; (void)c; (void)d; return 0; }
KSTUB int  native_verified_block_root_authorized(int a) { (void)a; return 0; }
KSTUB int  native_verified_block_volume_device(const char *a, int *b, char *c, unsigned long d)
{ (void)a; (void)b; (void)c; (void)d; return 0; }
KSTUB int  native_ram_block_volume_seed(const char *a, int *b, char *c, unsigned long d,
                                        unsigned long *e, unsigned long *f, unsigned char *g)
{ (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; (void)g; return 0; }
