/* SPDX-License-Identifier: Apache-2.0 */
/* Original QEMU 10 plugin. Count actual instruction execution callbacks and
 * data-memory callbacks. These are execution attempts, not retirement counts.
 * No physical-address classification: the pinned API lookup is unreliable for
 * some valid RISC-V helper accesses. The cost model uses uniform memory cost. */
#include <glib.h>
#include <qemu-plugin.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
QEMU_PLUGIN_EXPORT int qemu_plugin_version = QEMU_PLUGIN_VERSION;
typedef struct { uint64_t instructions, memory; } Counters;
static struct qemu_plugin_scoreboard *score;
static char *output;
static void translate(qemu_plugin_id_t id, struct qemu_plugin_tb *tb) {
    qemu_plugin_u64 count = qemu_plugin_scoreboard_u64_in_struct(score, Counters, instructions);
    qemu_plugin_u64 memory = qemu_plugin_scoreboard_u64_in_struct(score, Counters, memory);
    for (size_t i = 0; i < qemu_plugin_tb_n_insns(tb); ++i) {
        struct qemu_plugin_insn *insn = qemu_plugin_tb_get_insn(tb, i);
        qemu_plugin_register_vcpu_insn_exec_inline_per_vcpu(
            insn, QEMU_PLUGIN_INLINE_ADD_U64, count, 1);
        qemu_plugin_register_vcpu_mem_inline_per_vcpu(
            insn, QEMU_PLUGIN_MEM_RW, QEMU_PLUGIN_INLINE_ADD_U64, memory, 1);
    }
}
static void finish(qemu_plugin_id_t id, void *opaque) {
    FILE *file = fopen(output, "w");
    if (file) {
        qemu_plugin_u64 instructions = qemu_plugin_scoreboard_u64_in_struct(score, Counters, instructions);
        qemu_plugin_u64 memory = qemu_plugin_scoreboard_u64_in_struct(score, Counters, memory);
        fprintf(file, "{\"executed_instructions\":%" PRIu64
                ",\"data_accesses\":%" PRIu64 ",\"per_hart\":[",
                qemu_plugin_u64_sum(instructions), qemu_plugin_u64_sum(memory));
        for (int hart = 0; hart < qemu_plugin_num_vcpus(); ++hart) {
            fprintf(file, "%s{\"hart_id\":%d,\"executed_instructions\":%" PRIu64
                    ",\"data_accesses\":%" PRIu64 "}", hart ? "," : "", hart,
                    qemu_plugin_u64_get(instructions, hart), qemu_plugin_u64_get(memory, hart));
        }
        fprintf(file, "]}\n");
        fclose(file);
    }
    qemu_plugin_scoreboard_free(score); g_free(output);
}
QEMU_PLUGIN_EXPORT int qemu_plugin_install(qemu_plugin_id_t id,
    const qemu_info_t *info, int argc, char **argv) {
    if (!info->system_emulation || strcmp(info->target_name, "riscv64") || argc != 1
        || strncmp(argv[0], "output=", 7) || !argv[0][7]) return -1;
    output = g_strdup(argv[0]+7);
    score = qemu_plugin_scoreboard_new(sizeof(Counters));
    qemu_plugin_register_vcpu_tb_trans_cb(id, translate);
    qemu_plugin_register_atexit_cb(id, finish, NULL);
    return 0;
}
