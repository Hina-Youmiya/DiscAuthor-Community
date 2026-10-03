/* DiscAuthor: repair ONLY the known PCR adaptation-only one-ahead CC defect.
 * Never alter payload packets, timestamps, packet lengths or other header bits.
 * Usage: reexport-m2ts audit FILE|- REPORT | repair FILE REPORT
 * repair validates the entire input before modifying its private extracted copy.
 */
#include <CommonCrypto/CommonDigest.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>

typedef struct { uint64_t offset; int pid,cc,want,pcr; } Patch;
typedef struct {
 uint64_t bytes,packets,payload_packets,payload_bytes,pcr_packets,adaptation_only,repairs;
 char sha[65],protected_sha[65],payload_sha[65];
} Metrics;
static Patch *patches=NULL; static size_t count=0,capacity=0;
static char error_text[240];
static int fail(const char *reason,uint64_t offset,int pid) {
 snprintf(error_text,sizeof(error_text),"%s at byte %llu, PID %d",reason,(unsigned long long)offset,pid);return 0;
}
static int add(uint64_t offset,int pid,int cc,int want,int pcr) {
 if(count==capacity) {size_t n=capacity?capacity*2:1024;void *p=realloc(patches,n*sizeof(Patch));if(!p)return fail("Out of memory",offset,pid);patches=p;capacity=n;}
 patches[count++]=(Patch){offset,pid,cc,want,pcr};return 1;
}
static void hex(CC_SHA256_CTX *ctx,char out[65]) {
 unsigned char digest[32];CC_SHA256_Final(digest,ctx);
 for(int i=0;i<32;i++)sprintf(out+i*2,"%02x",digest[i]);out[64]=0;
}
static int scan(FILE *f,Metrics *m) {
 int payload_last[8192],first_payload[8192];
 for(int i=0;i<8192;i++)payload_last[i]=first_payload[i]=-1;
 memset(m,0,sizeof(*m));count=0;
 CC_SHA256_CTX full,protected,payload;CC_SHA256_Init(&full);CC_SHA256_Init(&protected);CC_SHA256_Init(&payload);
 unsigned char buf[192*4096];size_t n;
 while((n=fread(buf,1,sizeof(buf),f))>0) {
  if(n%192)return fail("Partial 192-byte M2TS packet",m->bytes,-1);
  CC_SHA256_Update(&full,buf,(CC_LONG)n);
  for(size_t off=0;off<n;off+=192) {
   unsigned char *packet=buf+off,*p=packet+4;uint64_t pos=m->bytes+off;
   if(p[0]!=0x47)return fail("Invalid transport sync",pos,-1);
   int pid=((p[1]&31)<<8)|p[2],afc=(p[3]>>4)&3,cc=p[3]&15;
   if(p[1]&128)return fail("Transport error indicator",pos,pid);
   if(p[3]&192)return fail("Scrambled transport is unsupported",pos,pid);
   if(!afc)return fail("Reserved adaptation control",pos,pid);
   int payload_offset=4,pcr=0;
   if(afc&2) {
    int length=p[4];payload_offset=5+length;
    if(payload_offset>188 || (afc==2 && length!=183) || (afc==3 && payload_offset>=188))
      return fail("Invalid adaptation length",pos,pid);
    if(length) {
     if(p[5]&128)return fail("Discontinuity flag outside the known repair scope",pos,pid);
     pcr=!!(p[5]&16);
     if(pcr && length<7)return fail("Truncated PCR",pos,pid);
    }
   }
   if(pcr)m->pcr_packets++;
   if(afc&1) {
    if(pid!=8191) {
     if(payload_last[pid]>=0 && cc!=((payload_last[pid]+1)&15))
       return fail("Payload continuity gap (not repairable)",pos,pid);
     if(first_payload[pid]<0)first_payload[pid]=cc;
     payload_last[pid]=cc;
    }
    CC_SHA256_Update(&payload,p+payload_offset,188-payload_offset);
    m->payload_packets++;m->payload_bytes+=188-payload_offset;
   } else {
    m->adaptation_only++;
    if(pid!=8191) {
     int want=payload_last[pid];
     if(want<0 || cc!=want) {
      if(want>=0 && (!pcr || cc!=((want+1)&15)))return fail("Adaptation counter outside known one-ahead PCR defect",pos,pid);
      if(!add(pos,pid,cc,want,pcr))return 0;
     }
    }
   }
   /* Mask ONLY the permitted four CC bits for protection of every other byte. */
   unsigned char saved=p[3];if(afc==2 && pcr)p[3]&=240;
   CC_SHA256_Update(&protected,packet,192);p[3]=saved;m->packets++;
  }
  m->bytes+=n;
 }
 if(ferror(f))return fail("Read error",m->bytes,-1);
 if(!m->packets)return fail("Empty M2TS stream",0,-1);
 size_t keep=0;
 for(size_t i=0;i<count;i++) {
  Patch p=patches[i];
  if(p.want<0) {
   if(first_payload[p.pid]<0)return fail("Adaptation-only PID has no payload reference",p.offset,p.pid);
   p.want=(first_payload[p.pid]+15)&15;
  }
  if(p.cc==p.want)continue;
  if(!p.pcr || p.cc!=((p.want+1)&15))return fail("Leading adaptation counter outside known PCR defect",p.offset,p.pid);
  patches[keep++]=p;
 }
 count=keep;m->repairs=count;
 hex(&full,m->sha);hex(&protected,m->protected_sha);hex(&payload,m->payload_sha);return 1;
}
static void metrics(FILE *f,const Metrics *m) {
 fprintf(f,"{\"bytes\":%llu,\"packets\":%llu,\"payload_packets\":%llu,\"payload_bytes\":%llu,\"pcr_packets\":%llu,\"adaptation_only_packets\":%llu,\"one_ahead_pcr_counters\":%llu,\"passed\":%s,\"sha256\":\"%s\",\"protected_sha256\":\"%s\",\"payload_sha256\":\"%s\"}",
 (unsigned long long)m->bytes,(unsigned long long)m->packets,(unsigned long long)m->payload_packets,(unsigned long long)m->payload_bytes,
 (unsigned long long)m->pcr_packets,(unsigned long long)m->adaptation_only,(unsigned long long)m->repairs,m->repairs?"false":"true",m->sha,m->protected_sha,m->payload_sha);
}
int main(int argc,char **argv) {
 if(argc!=4 || (strcmp(argv[1],"audit") && strcmp(argv[1],"repair")))return 2;
 int repair=!strcmp(argv[1],"repair"),is_stdin=!strcmp(argv[2],"-");
 if(repair && is_stdin)return 2;
 FILE *f=is_stdin?stdin:fopen(argv[2],repair?"r+b":"rb");if(!f){perror("open M2TS");return 3;}
 Metrics before,after;
 if(!scan(f,&before)){fprintf(stderr,"%s\n",error_text);if(!is_stdin)fclose(f);free(patches);return 4;}
 if(repair) {
  /* No byte is written until payload continuity and the entire stream pass. */
  for(size_t i=0;i<count;i++) {
   Patch p=patches[i];if(fseeko(f,(off_t)(p.offset+7),SEEK_SET))return 5;
   int byte=fgetc(f);if(byte==EOF || (byte&15)!=p.cc)return 5;
   if(fseeko(f,-1,SEEK_CUR) || fputc((byte&240)|p.want,f)==EOF)return 5;
  }
  if(fflush(f) || fsync(fileno(f)) || fseeko(f,0,SEEK_SET))return 5;
  if(!scan(f,&after)){fprintf(stderr,"%s\n",error_text);fclose(f);free(patches);return 6;}
  if(after.repairs || before.bytes!=after.bytes || before.packets!=after.packets ||
     strcmp(before.protected_sha,after.protected_sha) || strcmp(before.payload_sha,after.payload_sha))return 6;
 }
 if(!is_stdin)fclose(f);free(patches);
 FILE *report=fopen(argv[3],"w");if(!report)return 7;
 fprintf(report,"{\"schema_version\":1,\"mode\":\"%s\",\"before\":",argv[1]);metrics(report,&before);
 if(repair){fprintf(report,",\"after\":");metrics(report,&after);}
 fprintf(report,"}\n");return fclose(report)?7:0;
}
